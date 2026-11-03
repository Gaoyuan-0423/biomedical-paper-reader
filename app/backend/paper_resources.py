"""Locally curated data/code resources, with optional PDF provenance."""
import re
import unicodedata
from typing import Literal, Optional
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException
from pydantic import BaseModel, Field

from reading_workflow import load_record_list, source_info


class ResourceIn(BaseModel):
    title: str = Field(min_length=1, max_length=1000)
    kind: Literal['dataset', 'code', 'other'] = 'dataset'
    repository: str = Field(default='', max_length=200)
    accession: str = Field(default='', max_length=300)
    url: str = Field(default='', max_length=4000)
    role: Literal['generated', 'reused', 'implementation', 'unknown'] = 'unknown'
    organism: str = Field(default='', max_length=1000)
    tissue: str = Field(default='', max_length=1000)
    modality: str = Field(default='', max_length=1000)
    notes: str = Field(default='', max_length=12000)
    status: Literal['pending', 'verified'] = 'pending'
    document_id: str = ''
    page: Optional[int] = Field(default=None, ge=1, le=600, strict=True)
    quote: str = Field(default='', max_length=12000)


class ResourcePatch(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=1000)
    kind: Optional[Literal['dataset', 'code', 'other']] = None
    repository: Optional[str] = Field(default=None, max_length=200)
    accession: Optional[str] = Field(default=None, max_length=300)
    url: Optional[str] = Field(default=None, max_length=4000)
    role: Optional[Literal['generated', 'reused', 'implementation', 'unknown']] = None
    organism: Optional[str] = Field(default=None, max_length=1000)
    tissue: Optional[str] = Field(default=None, max_length=1000)
    modality: Optional[str] = Field(default=None, max_length=1000)
    notes: Optional[str] = Field(default=None, max_length=12000)
    status: Optional[Literal['pending', 'verified']] = None
    document_id: Optional[str] = None
    page: Optional[int] = Field(default=None, ge=1, le=600, strict=True)
    quote: Optional[str] = Field(default=None, max_length=12000)


RESOURCE_DERIVED_FIELDS = ('resources_count', 'resources_pending')
REPOSITORIES = {
    'geo': 'GEO', 'ncbigeo': 'GEO', 'geneexpressionomnibus': 'GEO',
    'sra': 'SRA', 'ncbisra': 'SRA', 'sequencereadarchive': 'SRA',
    'ena': 'ENA', 'europeannucleotidearchive': 'ENA',
    'gsa': 'GSA', 'cncbgsa': 'GSA', 'genomesequencearchive': 'GSA',
    'arrayexpress': 'ArrayExpress', 'github': 'GitHub', 'gitlab': 'GitLab',
    'zenodo': 'Zenodo', 'bioproject': 'BioProject', 'dbgap': 'dbGaP',
}
ACCESSION_RE = re.compile(r'^(?:GS[EM]\d+|[SED]R[APRSX]\d+|PRJ[END][AB]\d+|CRA\d+|HRA\d+|E-[A-Z]+-\d+|phs\d+(?:\.v\d+\.p\d+)?)$', re.I)


def load_resources(directory):
    def validate(record):
        if 'status' not in record or 'document_id' not in record or 'page' not in record:
            raise ValueError('missing resource state or source')
        for field in ('source_revision', 'created_at', 'updated_at'):
            if field in record and not isinstance(record[field], str):
                raise ValueError('invalid string field')
        fields = ResourceIn.model_validate(record).model_dump()
        return normalize_resource({**record, **fields})
    return load_record_list(directory, 'resources', validate)


def normalized_repository(value):
    value = unicodedata.normalize('NFKC', value).strip()
    key = re.sub(r'[\s_-]', '', value).casefold()
    return REPOSITORIES.get(key, value)


def resource_repository(value, accession):
    repository = normalized_repository(value)
    if not repository:
        if re.fullmatch(r'GS[EM]\d+', accession, re.I):
            return 'GEO'
        if re.fullmatch(r'SR[APRSX]\d+', accession, re.I):
            return 'SRA'
    return repository


def safe_url(value):
    value = value.strip()
    if not value:
        return ''
    if re.search(r'[\x00-\x20\x7f\\]', value):
        raise HTTPException(422, '资源链接不能包含空白、控制字符或反斜线')
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError()
        host = parsed.hostname.lower()
        if '%' in host or not host.strip('.'):
            raise ValueError()
        port = parsed.port
        host = '[' + host + ']' if ':' in host else host.encode('idna').decode('ascii')
        if port and not ((parsed.scheme.lower() == 'http' and port == 80) or (parsed.scheme.lower() == 'https' and port == 443)):
            host += ':' + str(port)
        return urlunsplit((parsed.scheme.lower(), host, parsed.path, parsed.query, parsed.fragment))
    except (ValueError, UnicodeError):
        raise HTTPException(422, '资源链接必须是无账号密码的完整 HTTP(S) 地址') from None


def normalize_resource(record):
    result = dict(record)
    for field in ResourceIn.model_fields:
        if field != 'page' and result.get(field) is None:
            raise HTTPException(422, f'{field} 不能为 null')
        if isinstance(result.get(field), str):
            result[field] = result[field].strip()
    if not result['title']:
        raise HTTPException(422, '请填写资源名称')
    result['repository'] = normalized_repository(result['repository'])
    result['accession'] = unicodedata.normalize('NFKC', result['accession']).strip()
    if re.search(r'\s', result['accession']):
        raise HTTPException(422, '每条资源请填写一个不含空白的仓库编号')
    if ACCESSION_RE.fullmatch(result['accession']):
        result['accession'] = result['accession'].upper()
    result['repository'] = resource_repository(result['repository'], result['accession'])
    result['url'] = safe_url(result['url'])
    if not result['accession'] and not result['url']:
        raise HTTPException(422, '请至少填写仓库编号或资源链接')
    if result['document_id']:
        if result['document_id'] != 'main' and not re.fullmatch(r'[0-9a-f]{32}', result['document_id']):
            raise HTTPException(422, '无效的来源文件 ID')
        if result['page'] is None:
            raise HTTPException(422, '选择来源文件后请填写 PDF 页码')
    elif result['page'] is not None:
        raise HTTPException(422, '填写页码时请同时选择来源文件')
    return result


def resource_key(record):
    accession = record.get('accession', '').casefold()
    if accession:
        return ('accession', resource_repository(record.get('repository', ''), accession).casefold(), accession)
    url = urlsplit(safe_url(record.get('url', '')))
    return ('url', urlunsplit((url.scheme, url.netloc, url.path or '/', url.query, '')))


def ensure_unique(records, record):
    key = resource_key(record)
    if any(r['id'] != record.get('id') and resource_key(r) == key for r in records):
        raise HTTPException(409, '这篇文献已记录相同仓库编号或资源链接，请编辑已有记录')


def validate_source(directory, meta, record):
    if not record.get('document_id'):
        return
    path, pages, _ = source_info(directory, meta, record['document_id'])
    if not path or not path.is_file():
        raise HTTPException(404, '资源依据对应的 PDF 不存在')
    if not 1 <= record['page'] <= pages:
        raise HTTPException(422, '资源依据页码超出该 PDF 的范围')


def source_revision(meta, record):
    return meta.get('document_revisions', {}).get(record['document_id'], 'legacy') if record.get('document_id') else ''


def public_resource(directory, meta, record):
    if not record.get('document_id'):
        available, changed, name = True, False, ''
    else:
        path, pages, name = source_info(directory, meta, record['document_id'])
        available = bool(path and path.is_file() and 1 <= record['page'] <= pages)
        changed = available and record.get('source_revision', 'legacy') != source_revision(meta, record)
    status = 'pending' if (not available or changed) and record['status'] == 'verified' else record['status']
    return {**record, 'source_available': available, 'source_changed': changed, 'source_name': name, 'status': status}


def public_resources(directory, meta):
    return [public_resource(directory, meta, record) for record in load_resources(directory)]


def resource_summary(directory, meta):
    records = public_resources(directory, meta)
    return {'resources_count': len(records), 'resources_pending': sum(r['status'] == 'pending' for r in records)}


def related_resources(entries, query=''):
    """entries are already public records, each carrying paper_id and paper_title."""
    groups = {}
    for record in entries:
        groups.setdefault(resource_key(record), {})[record['paper_id']] = record['paper_title']
    query = query.strip().casefold()
    output = []
    fields = ('title', 'paper_title', 'repository', 'accession', 'organism', 'tissue', 'modality', 'notes', 'url')
    for record in entries:
        if query and query not in ' '.join(str(record.get(field, '')) for field in fields).casefold():
            continue
        linked = [{'id': pid, 'title': title} for pid, title in groups[resource_key(record)].items() if pid != record['paper_id']]
        output.append({**record, 'related_papers': linked})
    return output
