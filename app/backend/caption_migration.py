"""Upgrade cached automatic captions, keeping all other paper data untouched."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import threading
import time
import weakref
from pathlib import Path

from figures import CAPTION_EXTRACTION_VERSION, _figure_records, extract_captions, load_figures, save_figures
from reading_workflow import atomic_json


_locks_guard = threading.Lock()
_locks = weakref.WeakValueDictionary()
_failures = {}
_RETRY_SECONDS = 60
_MARKER = 'caption-migrations.json'


def _paper_lock(directory):
    key = str(directory.resolve())
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def _source_stamp(source):
    try:
        stat = source.stat()
        return [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino]
    except OSError:
        return None


def _caption_fields(figure):
    # Observations are deliberately excluded: reading a figure must neither
    # invalidate this cache nor be overwritten by a delayed caption migration.
    return {key: figure.get(key) for key in
            ('id', 'document_id', 'label', 'page', 'bbox', 'parent', 'source', 'caption', 'caption_edited')}


def _signature(records):
    return hashlib.sha256(json.dumps([_caption_fields(item) for item in records],
        sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def _identity(figure):
    bbox = figure.get('bbox')
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        return (figure.get('label'), figure.get('page'), tuple(round(float(value), 2) for value in bbox))
    except (TypeError, ValueError):
        return None


def _automatic(figure):
    return figure.get('source', 'auto') == 'auto' and not figure.get('caption_edited')


def _expanded(old, new):
    # Unmarked legacy manual text is protected too: only complete an existing
    # automatic prefix, never replace a different or longer caption.
    old_text = ' '.join(str(old or '').split())
    new_text = ' '.join(str(new or '').split())
    return bool(old_text and len(new_text) > len(old_text) and new_text.startswith(old_text))


def _load_marker(directory):
    try:
        path = directory / _MARKER
        if path.stat().st_size > 4 * 1024 * 1024:
            return {}
        value = json.loads(path.read_text('utf-8'))
        return value.get('documents', {}) if isinstance(value, dict) and isinstance(value.get('documents'), dict) else {}
    except (OSError, UnicodeError, ValueError):
        return {}


def upgrade_captions(directory: Path, sources: dict[str, Path], storage_lock) -> list[dict]:
    """Lazy per-PDF upgrade; caller must not hold storage_lock while invoking.

    ``sources`` contains paths already authorized by the paper's metadata.
    Expensive parsing uses a byte snapshot outside the storage lock. Commit
    reloads current records and changes only still-matching automatic captions.
    """
    directory = Path(directory)
    with _paper_lock(directory):
        for document_id, source in sources.items():
            if document_id != 'main' and not re.fullmatch(r'[0-9a-f]{32}', document_id):
                continue
            source = Path(source)
            with storage_lock:
                try:
                    records = _figure_records(directory / 'figures.json')
                except (OSError, ValueError, UnicodeError):
                    # Reading a backup must preserve the damaged primary for
                    # inspection; only an explicit user save may repair it.
                    return load_figures(directory)
                own = [item for item in records if item.get('document_id', 'main') == document_id]
                if not any(_automatic(item) for item in own):
                    continue
                stamp = _source_stamp(source)
                if stamp is None:
                    continue
                signature = _signature(own)
                marker = _load_marker(directory).get(document_id, {})
                if (isinstance(marker, dict) and marker.get('algorithm') == CAPTION_EXTRACTION_VERSION
                        and marker.get('source_stat') == stamp and marker.get('figures') == signature):
                    continue
            retry_key = (str(source.resolve()), tuple(stamp), signature, CAPTION_EXTRACTION_VERSION)
            with _locks_guard:
                if _failures.get(retry_key, 0) > time.monotonic():
                    continue
            try:
                data = source.read_bytes()
                if _source_stamp(source) != stamp:
                    continue
                parsed = extract_captions(io.BytesIO(data))
            except Exception as exc:
                # Preserve old records and retry after a bounded cooldown. A
                # transient parser failure is not marked as a completed upgrade.
                logging.getLogger(__name__).warning('Caption upgrade deferred: %s', type(exc).__name__)
                with _locks_guard:
                    _failures[retry_key] = time.monotonic() + _RETRY_SECONDS
                    while len(_failures) > 256:
                        _failures.pop(next(iter(_failures)))
                continue
            index = {}
            for item in parsed:
                key = _identity(item)
                if key is not None:
                    index.setdefault(key, []).append(item['caption'])
            proposed = {}
            parent_captions = {}
            for item in own:
                matches = index.get(_identity(item), []) if not item.get('parent') else []
                if _automatic(item) and len(matches) == 1:
                    parent_captions[item['id']] = matches[0]
                    if _expanded(item.get('caption'), matches[0]):
                        proposed[item['id']] = matches[0]
            for item in own:
                parent_caption = parent_captions.get(item.get('parent'))
                if _automatic(item) and parent_caption and _expanded(item.get('caption'), parent_caption):
                    proposed[item['id']] = parent_caption
            original = {item['id']: _caption_fields(item) for item in own}
            with storage_lock:
                if _source_stamp(source) != stamp or not directory.is_dir():
                    continue
                try:
                    fresh = _figure_records(directory / 'figures.json')
                except (OSError, ValueError, UnicodeError):
                    continue
                changed = False
                for item in fresh:
                    replacement = proposed.get(item.get('id'))
                    if (replacement and item.get('document_id', 'main') == document_id
                            and _automatic(item) and _caption_fields(item) == original.get(item['id'])):
                        item['caption'] = replacement
                        changed = True
                if changed:
                    save_figures(directory, fresh)
                # Cache only the record set actually parsed. A concurrent new
                # crop/edit must be reconsidered on the next request.
                expected = [{**item, 'caption': proposed[item['id']]} if item['id'] in proposed else item for item in own]
                latest = [item for item in fresh if item.get('document_id', 'main') == document_id]
                if _signature(expected) == _signature(latest):
                    markers = _load_marker(directory)
                    markers[document_id] = {'algorithm': CAPTION_EXTRACTION_VERSION, 'source_stat': stamp,
                        'source_sha256': hashlib.sha256(data).hexdigest(), 'figures': _signature(latest)}
                    atomic_json(directory / _MARKER, {'schema': 1, 'documents': markers})
        with storage_lock:
            return load_figures(directory)
