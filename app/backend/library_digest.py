"""A read-only catalog of existing papers, notes and extracted figures."""
import hashlib
import json
import re
from collections import Counter, defaultdict
from functools import lru_cache

from fastapi import HTTPException

import project_libraries as pl
from chat_sources import file_revision
from figures import FigureStorageError, crop_source_changed, load_figures
from paper_tags import tag_key
from reading_card import reading_card
from reading_workflow import PAPER_SCHEMA_VERSION, defaults, source_info


PAPER_ID = re.compile(r'[0-9a-f]{32}')
FIGURE_ID = re.compile(r'[a-z][0-9]{3,6}')


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _file_stamp(path, directory):
    if path is None or not path.resolve().is_relative_to(directory.resolve()) or not path.is_file():
        return None
    try:
        stat = path.stat()
        return (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    except OSError:
        return None


@lru_cache(maxsize=512)
def _checked_revision(path, stamp):
    # One streaming digest per PDF revision even when a gallery contains many panels.
    return file_revision(path)


def _figure(directory, meta, record, library_id):
    doc = record.get('document_id', 'main')
    if not isinstance(doc, str) or (doc != 'main' and not PAPER_ID.fullmatch(doc)):
        return None
    fid, page = record['id'], record.get('page')
    pdf, pages, filename = source_info(directory, meta, doc)
    revisions = meta.get('document_revisions', {})
    revision = revisions.get(doc, 'legacy')
    png = directory / 'figures' / (fid + '.png')
    pdf_stamp, png_stamp = _file_stamp(pdf, directory), _file_stamp(png, directory)
    available = bool(pdf_stamp and type(page) is int and type(pages) is int and 1 <= page <= pages
                     and not crop_source_changed(record, revisions))
    identity = [directory.name, fid, doc, page, revision, pdf_stamp, png_stamp,
                record.get('bbox'), record.get('parent'), record.get('source')]
    token = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    url = f'/api/library/{library_id}/digest/figures/{directory.name}/{fid}'
    observation = _text(record.get('observation'))
    observation_stale = ('observation_source_revision' in record and
                         record.get('observation_source_revision') != revision)
    caption_stale = 'caption_source_revision' in record and record.get('caption_source_revision') != revision
    return dict(id=fid, paper_id=directory.name, paper_title=_text(meta.get('title')) or directory.name,
                kind=_text(record.get('figure_type')) or _text(record.get('kind')),
                label=_text(record.get('label')) or fid, caption='' if caption_stale else _text(record.get('caption')),
                caption_stale=caption_stale,
                observation=observation if not observation_stale and available else '', observation_stale=observation_stale,
                tags=record.get('tags', []), favorite=bool(record.get('favorite')), document_id=doc,
                document_name=(_text(meta.get('filename')) or filename) if doc == 'main' else filename,
                page=page if type(page) is int else None, bbox=None, key=token,
                source_revision=revision, material_revision=meta.get('material_revision', 0),
                source_available=available, source_url=f'{url}/source?key={token}' if available else '',
                image_url=f'{url}/image?key={token}' if available and png_stamp else '')


def _authors(value):
    if isinstance(value, str):
        value = value.split(';')
    return [_text(author) for author in value if _text(author)] if isinstance(value, list) else []


def read_digest_meta(directory):
    """Read one metadata file; gallery indexing needs no resource/evidence annotation."""
    path = directory / 'meta.json'
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError('metadata is outside this paper')
    meta = json.loads(path.read_text('utf-8'))
    if not isinstance(meta, dict) or meta.get('id', directory.name) != directory.name:
        raise ValueError('paper identity mismatch')
    if int(meta.get('schema_version', 1)) > PAPER_SCHEMA_VERSION:
        raise ValueError('newer paper format')
    return defaults(meta)


def build_digest(root, papers_dir, library_id, read_meta=read_digest_meta):
    """The paper id is the archival identity, including across overlapping projects."""
    projects = pl.projects(root)
    memberships = defaultdict(list)
    for group in projects['libraries']:
        for pid in set(group.paper_ids):
            memberships[pid].append(dict(id=group.id, name=group.name, archived=group.archived))
    if library_id == 'all':
        library = dict(id='all', name='全部文献', topic='')
        ids = (p.name for p in papers_dir.iterdir()
               if PAPER_ID.fullmatch(p.name) and p.is_dir() and (p / 'meta.json').is_file()) if papers_dir.is_dir() else ()
    else:
        group = pl.active_project(root, library_id)
        library = dict(id=group.id, name=group.name, topic=group.topic)
        ids = group.paper_ids
    cards, figures = [], []
    topic_counts, topic_names = Counter(), {}
    unavailable, damaged_figures = 0, 0
    for pid in sorted(set(ids)):
        directory = papers_dir / pid
        if directory.is_symlink() or not (directory / 'meta.json').is_file():
            unavailable += 1
            continue
        try:
            meta = read_meta(directory)
            if meta.get('id', pid) != pid:
                raise ValueError('paper identity mismatch')
            card = reading_card(directory, meta)
        except (ValueError, KeyError, TypeError, AttributeError, OSError, HTTPException):
            unavailable += 1
            continue
        try:
            paper_figures = [_figure(directory, meta, fig, library_id) for fig in load_figures(directory)]
            paper_figures = [item for item in paper_figures if item is not None]
        except (FigureStorageError, ValueError, TypeError, KeyError, OSError):
            paper_figures = []
            damaged_figures += 1
        figures.extend(paper_figures)
        tags = []
        seen_tags = set()
        for value in meta.get('tags') or []:
            tag = _text(value)
            key = tag_key(tag)
            if key and key not in seen_tags:
                seen_tags.add(key)
                tags.append(topic_names.setdefault(key, tag))
                topic_counts[key] += 1
        card.update(id=pid, title=_text(meta.get('title')) or pid, authors=_authors(meta.get('authors')),
                    year=str(meta.get('year') or ''), journal=_text(meta.get('journal')), doi=_text(meta.get('doi')),
                    tags=tags, libraries=memberships[pid], has_pdf=bool(_file_stamp(directory / 'paper.pdf', directory)),
                    figure_count=len(paper_figures), material_revision=meta.get('material_revision', 0),
                    notes_revision=meta.get('notes_revision', 0))
        cards.append(card)
    cards.sort(key=lambda c: (c['title'].casefold(), c['id']))
    figures.sort(key=lambda f: (f['paper_title'].casefold(), f['paper_id'], f['document_id'] != 'main',
                                f['document_id'], f['page'] or 0, f['id']))
    topics = [dict(name=topic_names[key], count=count) for key, count in
              sorted(topic_counts.items(), key=lambda item: (-item[1], item[0]))]
    counts = dict(papers=len(cards), with_notes=sum(bool(c['has_notes']) for c in cards),
                  with_figures=sum(bool(c['figure_count']) for c in cards), figures=len(figures),
                  with_pdf=sum(c['has_pdf'] for c in cards), unclassified=sum(not c['tags'] for c in cards),
                  unavailable=unavailable, unavailable_figures=sum(not f['image_url'] for f in figures),
                  damaged_figure_records=damaged_figures, unavailable_libraries=projects['unavailable'])
    return dict(library=library, counts=counts, topics=topics, papers=cards, figures=figures)


def current_figure(papers_dir, pid, fid, key, library_id='all'):
    if not PAPER_ID.fullmatch(pid) or not FIGURE_ID.fullmatch(fid) or not re.fullmatch('[0-9a-f]{64}', key):
        raise HTTPException(404, '插图来源不存在')
    directory = papers_dir / pid
    try:
        if directory.is_symlink():
            raise ValueError()
        meta = read_digest_meta(directory)
        records = load_figures(directory)
        record = next((f for f in records if f['id'] == fid), None)
        item = _figure(directory, meta, record, library_id) if record else None
    except (OSError, ValueError, TypeError, KeyError, FigureStorageError):
        item = None
    if not item or key != item['key'] or not item['source_available']:
        raise HTTPException(409, '插图来源已变更，请刷新文献库整理')
    revision = item['source_revision']
    if isinstance(revision, str) and re.fullmatch('[0-9a-f]{64}', revision):
        path, _, _ = source_info(directory, meta, item['document_id'])
        if _checked_revision(path, _file_stamp(path, directory)) != revision:
            raise HTTPException(409, '插图对应的 PDF 内容已变化')
    return item


def figure_image_path(papers_dir, figure):
    """Resolve exactly the local figure represented by a previous digest row."""
    try:
        current = current_figure(papers_dir, figure['paper_id'], figure['id'], figure['key'])
    except (HTTPException, KeyError):
        return None
    if current['image_url']:
        return papers_dir / current['paper_id'] / 'figures' / (current['id'] + '.png')
    return None
