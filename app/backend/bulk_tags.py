"""One durable undo receipt per library, covering only newly added paper tags."""
import hashlib
import json
import re
from typing import Annotated

from fastapi import HTTPException
from pydantic import BaseModel, Field, StringConstraints

from paper_tags import TOTAL_LIMIT, tag_key
from project_libraries import HexId
from reading_workflow import atomic_json


class AddTags(BaseModel):
    request_id: HexId
    previous_id: HexId | None = None
    tag: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    paper_ids: list[HexId] = Field(min_length=1)


def receipt_path(root, library_id):
    if library_id != 'all' and not re.fullmatch(r'[0-9a-f]{32}', library_id):
        raise HTTPException(404, '未找到课题库')
    return root / 'bulk-paper-tags' / (library_id + '.json')


def receipt(path):
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text('utf-8'))
        if record['phase'] not in ('adding', 'added', 'removing', 'removed'):
            raise ValueError()
        if not isinstance(record['targets'], dict) or not isinstance(record['tag'], str):
            raise ValueError()
        if not all(re.fullmatch('[0-9a-f]{32}', key) for key in record['targets']):
            raise ValueError()
        return record
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise HTTPException(409, '批量标签记录无法读取') from exc


def public(record):
    if not record:
        return {'id': None, 'undo': False}
    return {'id': record['id'], 'undo': record['phase'] == 'added' and bool(record['targets']),
            'tag': record['tag'], 'count': len(record['targets'])}


def finish(path, record, papers, trash, read_meta, write_meta, members=None):
    """Replay interrupted writes under the storage lock. Ownership survives unrelated edits."""
    if not record or record['phase'] not in ('adding', 'removing'):
        return
    adding = record['phase'] == 'adding'
    tag, owner = record['tag'], record['id']
    applied = {}
    for pid, revision in record['targets'].items():
        directory = papers / pid
        if not (directory / 'meta.json').is_file() and not adding:
            directory = trash / pid
        if not (directory / 'meta.json').is_file():
            continue
        try:
            meta = read_meta(directory)
        except HTTPException as exc:
            if exc.status_code == 409:  # A newer paper schema cannot be edited.
                continue
            raise
        owners = meta.get('batch_tag_owners', {})
        if adding:
            if owners.get(tag) == owner and tag in meta['tags']:
                applied[pid] = revision
                continue
            if (members is not None and pid not in members) or meta['tags_revision'] != revision or tag in meta['tags']:
                continue
            meta['tags'].append(tag)
            meta.setdefault('batch_tag_owners', {})[tag] = owner
            applied[pid] = revision
        else:
            if owners.get(tag) != owner or tag not in meta['tags']:
                continue
            meta['tags'].remove(tag)
            del owners[tag]
            if not owners:
                meta.pop('batch_tag_owners', None)
        meta['tags_revision'] += 1
        write_meta(directory, meta)
    if adding:
        record['targets'] = applied
    record['phase'] = 'added' if adding else 'removed'
    atomic_json(path, record)


def apply(path, previous, body, members, papers, read_meta):
    ids = set(body.paper_ids)
    signature = hashlib.sha256(json.dumps([body.tag, sorted(ids)], ensure_ascii=False).encode()).hexdigest()
    if previous and previous['id'] == body.request_id:
        if previous.get('request') != signature or previous['phase'] == 'removed':
            raise HTTPException(409, '批量标签记录已变更，请重新打开操作')
        return previous
    if body.previous_id != (previous or {}).get('id'):
        raise HTTPException(409, '批量标签记录已变更，请重新打开操作')
    if not ids.issubset(members):
        raise HTTPException(409, '结果中的文献已移出课题或不可编辑，请刷新文献列表')
    label = tag_key(body.tag)
    if not label or any(ord(char) < 32 for char in body.tag):
        raise HTTPException(422, '请输入 1–40 个字符的单行标签')
    targets, existing = {}, 0
    for pid in ids:
        meta = read_meta(papers / pid)
        if label in {tag_key(item) for item in meta['tags']}:
            existing += 1
        elif len(meta['tags']) < TOTAL_LIMIT:
            targets[pid] = meta['tags_revision']
    record = {'id': body.request_id, 'tag': body.tag, 'targets': targets, 'phase': 'adding',
              'existing': existing, 'total': len(ids), 'request': signature}
    if targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, record)
    else:
        record['phase'] = 'added'
    return record
