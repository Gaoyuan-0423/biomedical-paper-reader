"""Local project comparisons. User judgments and their cited sources are saved separately from papers."""
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

from chat_sources import file_revision
from figures import FigureStorageError, crop_source_changed, load_figures
from paper_resources import load_resources, resource_accessions
from reading_workflow import SidecarError, atomic_json, load_evidence, public_evidence, source_info


Id = Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]
Reference = Annotated[str, Field(max_length=160, min_length=1)]


class ComparisonRow(BaseModel):
    paper_id: Id
    population: str = Field(default='', max_length=4000)
    contrast: str = Field(default='', max_length=4000)
    relation: Literal['unassessed', 'supports', 'qualifies', 'contradicts', 'unreported'] = 'unassessed'
    note: str = Field(default='', max_length=16000)
    references: list[Reference] = Field(default_factory=list, max_length=100)


class ComparisonIn(BaseModel):
    revision: int = Field(ge=0, strict=True)
    question: str = Field(min_length=1, max_length=2000)
    synthesis: str = Field(default='', max_length=32000)
    rows: list[ComparisonRow] = Field(min_length=2, max_length=100)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def make_source(kind, identity, document, page, label, text='', figure_id='', status='', available=True, bbox=None, figure_label=''):
    content = dict(kind=kind, document_id=document['id'], document_name=document['name'],
                   page=page, label=label, text=text, figure_id=figure_id, figure_label=figure_label, status=status)
    revision = document['revision']
    key = f'{kind}:{identity}:{_digest([content, revision, bbox])}'
    return dict(content, key=key, available=available)


def material(directory, meta):
    docs = {}
    revisions = meta.get('document_revisions', {})
    for doc_id in ['main'] + [s['id'] for s in meta.get('supplements', [])]:
        path, pages, name = source_info(directory, meta, doc_id)
        if path and path.is_file() and pages:
            revision = revisions.get(doc_id, 'legacy')
            docs[doc_id] = dict(id=doc_id, name=(meta.get('filename') or name) if doc_id == 'main' else name,
                                pages=pages, metadata_revision=revision,
                                revision=file_revision(path) if revision == 'legacy' else revision)
    sources, warnings, accessions, samples = [], [], set(), []
    try:
        figures = {f['id']: f for f in load_figures(directory)
                   if (directory / 'figures' / (f['id'] + '.png')).is_file()}
    except FigureStorageError as error:
        warnings.append(str(error))
        figures = {}
    try:
        for row in load_evidence(directory):
            item = public_evidence(directory, meta, row)
            doc = docs.get(item['document_id']) or dict(id=item['document_id'], name=item['source_name'], revision='')
            figure_id = item.get('figure_id', '')
            figure = figures.get(figure_id, {})
            linked = not figure_id or (figure.get('page') == item['page'] and figure.get('document_id', 'main') == doc['id']
                                       and not crop_source_changed(figure, revisions))
            sources.append(make_source('evidence', item['id'], doc, item['page'], item['claim'], item.get('quote', ''),
                                       figure_id, item['status'], linked and item['source_available'] and not item['source_changed'],
                                       bbox=[figure.get('bbox'), item.get('selection_bbox')], figure_label=figure.get('label') or figure_id))
    except SidecarError as error:
        warnings.append(error.message)
    for fig in figures.values():
        doc = docs.get(fig.get('document_id', 'main'))
        page = fig.get('page')
        if doc and type(page) is int and 1 <= page <= doc['pages']:
            sources.append(make_source('figure', fig['id'], doc, page, fig.get('label') or fig['id'],
                                       fig.get('caption') or '', fig['id'], available=not crop_source_changed(fig, revisions),
                                       bbox=fig.get('bbox')))
    try:
        for item in load_resources(directory):
            accessions.update(resource_accessions(item))
            sample = {k: item.get(k, '') for k in ('organism', 'tissue', 'sample_groups', 'sample_count')}
            if any(sample.values()):
                samples.append({**sample, 'title': item.get('title', ''), 'identifiers': item.get('identifiers', '') or item.get('accession', '')})
    except SidecarError as error:
        warnings.append(error.message)
    return dict(paper_id=meta['id'], title=meta.get('title') or meta['id'], documents=list(docs.values()),
                sources=sources, accessions=sorted(accessions), samples=samples, warnings=warnings)


def resolve_source(key, materials):
    if key.startswith('page:'):
        parts = key.split(':')
        if len(parts) != 4 or not re.fullmatch(r'[1-9][0-9]{0,2}', parts[2]):
            return None
        doc = next((d for d in materials['documents'] if d['id'] == parts[1] and d['revision'] == parts[3]), None)
        if doc and int(parts[2]) <= doc['pages']:
            return dict(key=key, kind='page', document_id=doc['id'], document_name=doc['name'],
                        page=int(parts[2]), label='', text='', figure_id='', status='', available=True)
        return None
    return next((s for s in materials['sources'] if s['key'] == key and s['available']), None)


def scope_dir(root, library_id):
    if library_id != 'all' and not re.fullmatch(r'[0-9a-f]{32}', library_id):
        raise HTTPException(404, '课题库不存在')
    return root / 'comparisons' / library_id


def record_path(root, library_id, comparison_id):
    if not re.fullmatch(r'[0-9a-f]{32}', comparison_id):
        raise HTTPException(404, '比较记录不存在')
    return scope_dir(root, library_id) / (comparison_id + '.json')


def load(path):
    if not path.is_file():
        raise HTTPException(404, '比较记录不存在')
    try:
        record = json.loads(path.read_text('utf-8'))
        if record['version'] != 1 or record['id'] != path.stem:
            raise ValueError()
        parsed = ComparisonIn.model_validate(record)
        if parsed.revision < 1 or not record['updated_at'] or len({r.paper_id for r in parsed.rows}) != len(parsed.rows):
            raise ValueError()
        for row in record['rows']:
            if not isinstance(row['title'], str) or {s['key'] for s in row['sources']} != set(row['references']):
                raise ValueError()
        return record
    except (OSError, UnicodeError, KeyError, AttributeError, TypeError, ValueError, ValidationError) as exc:
        raise HTTPException(409, '比较记录无法读取，原文件已保留。') from exc


def summaries(root, library_id):
    result = []
    for path in scope_dir(root, library_id).glob('*.json'):
        record = load(path)
        result.append({key: record[key] for key in ('id', 'question', 'updated_at', 'revision')})
    return sorted(result, key=lambda item: (item['updated_at'], item['id']), reverse=True)


def save(root, library_id, comparison_id, body, allowed_ids, get_material):
    path = record_path(root, library_id, comparison_id)
    previous = load(path) if path.exists() else {}
    fingerprint = _digest(body.model_dump())
    if previous.get('request_digest') == fingerprint:
        return previous
    if previous.get('revision', 0) != body.revision:
        raise HTTPException(409, '比较记录已在其他窗口更新，请重新打开后合并修改。')
    if not body.question.strip() or len({r.paper_id for r in body.rows}) != len(body.rows):
        raise HTTPException(422, '请填写研究问题，并选择至少两篇不同的论文。')
    old_rows = {r['paper_id']: r for r in previous.get('rows', [])}
    rows = []
    for row in body.rows:
        if len(set(row.references)) != len(row.references):
            raise HTTPException(422, '同一篇论文的证据不可重复选择。')
        old = old_rows.get(row.paper_id, {})
        if row.paper_id not in allowed_ids and not old:
            raise HTTPException(409, '所选论文已不在当前课题库。')
        old_sources = {s['key']: s for s in old.get('sources', [])}
        # Previously cited material is historical even after a paper is removed.
        needs_material = not old or any(key not in old_sources for key in row.references)
        current = get_material(row.paper_id) if needs_material and row.paper_id in allowed_ids else {}
        sources = []
        for key in row.references:
            source = old_sources.get(key) or (resolve_source(key, current) if current else None)
            if not source:
                raise HTTPException(409, '所选证据或原文已变更，请刷新材料后重新选择。')
            sources.append(source)
        rows.append(dict(row.model_dump(), title=current.get('title') or old['title'], sources=sources))
    record = dict(version=1, id=comparison_id, revision=body.revision + 1, question=body.question.strip(),
                  synthesis=body.synthesis, rows=rows, request_digest=fingerprint,
                  updated_at=datetime.now(timezone.utc).isoformat())
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, record)
    return record
