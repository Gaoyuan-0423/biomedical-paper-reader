"""Paper-local provenance for individual sample fields."""
import re
import unicodedata
from typing import Literal, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field, TypeAdapter

from chat_sources import document_source, file_revision, load_document_pages

SampleField = Literal['organism', 'tissue', 'disease', 'modality', 'platform', 'sample_groups', 'sample_count']
FIELD_LABELS = {'organism': '物种', 'tissue': '组织／部位', 'disease': '疾病／状态', 'modality': '技术类型',
                'platform': '平台', 'sample_groups': '实验分组', 'sample_count': '论文实际样本量'}
SOURCE_LABELS = {'located': '指定页找到摘录', 'unmatched': '指定页未找到摘录', 'changed': '来源文件已更新',
                 'missing': '来源已移除或页码失效', 'unavailable': '来源文件暂时无法读取', 'value_changed': '字段值已修改'}


class FieldSourceIn(BaseModel):
    document_id: str = Field(pattern=r'^(main|[0-9a-f]{32})$')
    page: int = Field(ge=1, le=600, strict=True)
    quote: str = Field(min_length=1, max_length=6000)
    expected_revision: Optional[str] = Field(default=None, max_length=64)


class SavedFieldSource(FieldSourceIn):
    value: str = Field(min_length=1, max_length=1000)
    source_name: str
    source_revision: str = Field(pattern=r'^[0-9a-f]{64}$')
    size: int = Field(ge=0, strict=True)
    mtime_ns: int = Field(strict=True)
    matched: bool = Field(strict=True)


FieldChanges = dict[SampleField, Optional[FieldSourceIn]]
_saved = TypeAdapter(dict[SampleField, SavedFieldSource])


def validate_saved(sources):
    _saved.validate_python(sources)


def _clean(text):
    return ' '.join(unicodedata.normalize('NFKC', text).split())


def apply_field_sources(directory, meta, record, changes):
    if not changes:
        return
    sources = dict(record.get('field_sources', {}))
    pages = {}
    for field, source in changes.items():
        if source is None:
            sources.pop(field, None)
            continue
        if not record[field] or not source.quote.strip():
            raise HTTPException(422, '请填写样品字段值和原文摘录')
        key = (source.document_id, source.page)
        if key not in pages:
            path, count, name = document_source(directory, meta, source.document_id)
            if source.page > count:
                raise HTTPException(422, '字段出处页码超出该 PDF 的范围')
            stamp = path.stat()
            blocks = load_document_pages(directory, meta, source.document_id, [source.page])
            revision = blocks[0]['source_revision'] if blocks else file_revision(path)
            if path.stat().st_mtime_ns != stamp.st_mtime_ns:
                raise HTTPException(409, '提取原文期间文件已变化，请重试')
            pages[key] = (name, revision, stamp, _clean(' '.join(block['text'] for block in blocks)))
        name, revision, stamp, text = pages[key]
        if source.expected_revision and source.expected_revision != revision:
            raise HTTPException(409, '原文选区来自旧版本，请从当前 PDF 重新选择')
        quote = source.quote.strip()
        needle = _clean(quote)
        pattern = (r'(?<!\d)' if needle[:1].isdecimal() else '') + re.escape(needle) + (r'(?!\d)' if needle[-1:].isdecimal() else '')
        sources[field] = {**source.model_dump(exclude={'expected_revision'}), 'quote': quote, 'value': record[field], 'source_name': name,
                          'source_revision': revision, 'size': stamp.st_size, 'mtime_ns': stamp.st_mtime_ns,
                          'matched': bool(needle and re.search(pattern, text))}
    if sources:
        record['field_sources'] = sources
    else:
        record.pop('field_sources', None)


def public_field_source(directory, meta, source, value, verify_bytes=False):
    state = 'located' if source['matched'] else 'unmatched'
    try:
        path, pages, _ = document_source(directory, meta, source['document_id'])
        if pages < source['page']:
            state = 'missing'
        else:
            stamp = path.stat()
            same_stamp = (stamp.st_size, stamp.st_mtime_ns) == (source['size'], source['mtime_ns'])
            if (verify_bytes or not same_stamp) and file_revision(path) != source['source_revision']:
                state = 'changed'
            elif value != source['value']:
                state = 'value_changed'
    except HTTPException as exc:
        state = 'missing' if exc.status_code == 404 else 'unavailable'
    except OSError:
        state = 'unavailable'
    result = {name: source[name] for name in ('document_id', 'page', 'quote', 'value', 'source_name')}
    return {**result, 'state': state}


def public_field_sources(directory, meta, record):
    return {field: public_field_source(directory, meta, source, record[field])
            for field, source in record.get('field_sources', {}).items()}


def field_source_lines(record):
    return [f"{FIELD_LABELS[field]}（{source['value']}） · {source['source_name']} · PDF 第 {source['page']} 页"
            f" · {SOURCE_LABELS[source['state']]} · {source['quote']}"
            for field in FIELD_LABELS if (source := record.get('field_sources', {}).get(field))]
