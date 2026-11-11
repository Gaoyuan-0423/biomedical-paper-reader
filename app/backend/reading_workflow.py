"""Actionable reading tasks and source-bound evidence records."""
import json
import os
import re
import tempfile
from pathlib import Path
from threading import RLock
from fastapi import HTTPException


READING_STATUSES = ('unread', 'skimmed', 'reading', 'completed')
READING_DECISIONS = ('pending', 'priority', 'reference', 'irrelevant')
DERIVED_FIELDS = ('tasks', 'notes_stale', 'evidence_count', 'evidence_pending', 'storage_warnings')
# Bump when an older build would misread data written by the new version; papers stamped
# with a larger schema_version are refused instead of interpreted.
PAPER_SCHEMA_VERSION = 1
# Evidence writes are short local transactions; one lock avoids lost updates.
evidence_lock = RLock()
storage_lock = evidence_lock


class SidecarError(HTTPException):
    """A preserved local record file needs inspection before it can be used."""
    def __init__(self, kind):
        self.kind = kind
        self.message = (f'本篇文献的 {kind}.json 无法读取或记录格式损坏。原文件已保留，'
                        '请先备份并检查该文件；相关记录暂不能读取、修改或导出。')
        super().__init__(status_code=409, detail=self.message)


def load_record_list(directory, kind, validate):
    path = directory / (kind + '.json')
    if not path.exists():
        return []
    try:
        records = json.loads(path.read_text('utf-8'))
        if not isinstance(records, list):
            raise ValueError('not an array')
        result, ids = [], set()
        for record in records:
            if not isinstance(record, dict):
                raise ValueError('not an object')
            rid = record.get('id')
            if not isinstance(rid, str) or not re.fullmatch(r'[0-9a-f]{32}', rid) or rid in ids:
                raise ValueError('invalid or duplicate id')
            ids.add(rid)
            result.append(validate(record))
        return result
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, HTTPException) as exc:
        raise SidecarError(kind) from exc


def atomic_json(path, value):
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix='.' + path.stem + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def defaults(meta):
    # Keep legacy values readable without using them to derive tasks.
    meta.setdefault('schema_version', PAPER_SCHEMA_VERSION)
    if meta.get('reading_status') not in READING_STATUSES:
        meta['reading_status'] = 'unread'
    if meta.get('reading_decision') not in READING_DECISIONS:
        meta['reading_decision'] = 'pending'
    meta.setdefault('notes_incomplete', False)
    meta.setdefault('material_revision', 0)
    meta.setdefault('notes_material_revision', meta['material_revision'])
    meta.setdefault('document_revisions', {})
    return meta


def materials_changed(meta, document_id, revision):
    defaults(meta)
    if meta['document_revisions'].get(document_id) == revision:
        return
    meta['document_revisions'][document_id] = revision
    meta['material_revision'] += 1


def load_evidence(directory):
    def validate(record):
        doc, page = record.get('document_id'), record.get('page')
        if record.get('status') not in ('pending', 'verified', 'rejected'):
            raise ValueError('invalid status')
        if not isinstance(record.get('claim'), str) or not record['claim'].strip():
            raise ValueError('missing claim')
        if not isinstance(doc, str) or (doc != 'main' and not re.fullmatch(r'[0-9a-f]{32}', doc)):
            raise ValueError('invalid source')
        if type(page) is not int or not 1 <= page <= 600:
            raise ValueError('invalid page')
        for field in ('quote', 'figure_id', 'source_revision', 'created_at', 'updated_at'):
            if field in record and not isinstance(record[field], str):
                raise ValueError('invalid string field')
        return record
    return load_record_list(directory, 'evidence', validate)


def source_info(directory, meta, document_id):
    if document_id == 'main':
        return directory / 'paper.pdf', meta.get('pages', 0), '正文'
    source = next((s for s in meta.get('supplements', []) if s['id'] == document_id), None)
    if source is None:
        return None, 0, '已移除的补充材料'
    return directory / 'supplements' / (document_id + '.pdf'), source['pages'], source['filename']


def public_evidence(directory, meta, record):
    path, pages, name = source_info(directory, meta, record['document_id'])
    available = bool(path and path.is_file() and 1 <= record['page'] <= pages)
    revision = meta.get('document_revisions', {}).get(record['document_id'], 'legacy')
    changed = available and record.get('source_revision', 'legacy') != revision
    return {**record, 'source_available': available, 'source_changed': changed,
            'source_name': name, 'status': 'pending' if (changed or not available) and record['status'] == 'verified' else record['status']}


def annotate(directory, meta):
    defaults(meta)
    meta['storage_warnings'] = []
    try:
        records = [public_evidence(directory, meta, r) for r in load_evidence(directory)]
        meta['evidence_count'] = len(records)
        meta['evidence_pending'] = sum(r['status'] == 'pending' for r in records)
    except SidecarError as exc:
        meta['evidence_count'] = meta['evidence_pending'] = None
        meta['storage_warnings'].append({'kind': exc.kind, 'message': exc.message})
    meta['notes_stale'] = bool(meta.get('notes', '').strip()) and meta['notes_material_revision'] != meta['material_revision']
    tasks = []
    def task(id, label, count=None):
        tasks.append({'id': id, 'label': label, **({'count': count} if count is not None else {})})
    if not meta.get('has_file'):
        task('fulltext', '补充正文 PDF')
    if meta.get('notes_incomplete'):
        task('notes_incomplete', '完成未完成的笔记')
    if meta['notes_stale']:
        task('notes_stale', '核对材料变更后的笔记')
    if meta['evidence_pending']:
        task('evidence', '核查关键证据', meta['evidence_pending'])
    meta['tasks'] = tasks
    return meta
