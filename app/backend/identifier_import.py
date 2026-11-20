"""Preview scholarly identifiers against the local library before applying selected rows."""
import asyncio
import re
import time
import uuid
from collections import OrderedDict
from threading import Lock
from urllib.parse import unquote, urlencode, urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from external import DOI_RE, LookupError_, _get_json, _strip_jats, fetch_crossref
from llm import UnsafeURLError

MAX_ITEMS = 50
PMC_HOST = 'www.ebi.ac.uk'
_cache = OrderedDict()
_cache_lock = Lock()


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=20000)
    retry_preview_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    library_id: str = Field(default='all', pattern=r'^(all|[0-9a-f]{32})$')


class ApplyIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    preview_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    keys: list[int] = Field(min_length=1, max_length=MAX_ITEMS)


def identify(raw):
    value = raw.strip()
    if value.lower().startswith(('https://', 'http://')):
        try:
            url = urlsplit(value)
            path = unquote(url.path).strip('/')
            host = url.hostname or ''
        except ValueError:
            return None
        if host == 'pubmed.ncbi.nlm.nih.gov':
            value = path
        elif host in ('www.ncbi.nlm.nih.gov', 'ncbi.nlm.nih.gov') and path.lower().startswith('pubmed/'):
            value = path.split('/', 1)[1]
        elif host in ('europepmc.org', 'www.europepmc.org') and re.fullmatch(r'article/med/[0-9]+', path, re.I):
            value = path.rsplit('/', 1)[1]
        else:
            doi = re.search(r'(?:^|/)(10\.\d{4,9}/.+)$', path, re.I)
            value = doi.group(1) if doi else ''
    pmid = re.fullmatch(r'(?:PMID:)?([1-9][0-9]{0,8})', value, re.I)
    if pmid:
        return ('pmid', pmid.group(1))
    value = re.sub(r'^doi:', '', value, flags=re.I)
    if DOI_RE.fullmatch(value):
        return ('doi', value.lower())
    return None


def split_input(text):
    # Semicolons, parentheses and periods may be part of a DOI suffix.
    text = re.sub(r'\b(PMID|DOI)\s*:\s*', r'\1:', text, flags=re.I)
    tokens = re.split(r'[\s,，；]+', text.strip())
    tokens = [token for token in tokens if token]
    if not 1 <= len(tokens) <= MAX_ITEMS or any(len(t) > 1024 for t in tokens):
        raise HTTPException(422, '每次请输入 1–50 个 DOI 或 PMID，每项最多 1024 个字符')
    return tokens


async def fetch_pubmed(pmid):
    query = urlencode({'query': f'EXT_ID:{pmid} AND SRC:MED', 'resultType': 'core', 'format': 'json', 'pageSize': 1})
    data = await _get_json(f'https://{PMC_HOST}/europepmc/webservices/rest/search?{query}', PMC_HOST)
    for row in data.get('resultList', {}).get('result', []):
        if row.get('source') != 'MED' or str(row.get('pmid') or row.get('id')) != pmid:
            continue
        journal = (row.get('journalInfo') or {}).get('journal') or {}
        return {'pmid': pmid, 'doi': (row.get('doi') or '').strip().lower(), 'title': _strip_jats(row.get('title') or ''),
                'authors': row.get('authorString') or '', 'journal': journal.get('title') or journal.get('medlineAbbreviation') or '',
                'year': str(row.get('pubYear') or ''), 'abstract': _strip_jats(row.get('abstractText') or ''),
                'url': f'https://pubmed.ncbi.nlm.nih.gov/{pmid}/'}
    raise LookupError_('未找到该 PMID 的 PubMed 记录')


def aliases(record):
    return [(kind, str(record[kind]).strip().lower()) for kind in ('doi', 'pmid') if record.get(kind)]


def library_index(active, trash):
    index = {}
    for status, records in [('trashed', trash), ('existing', active)]:
        for item in records:
            for alias in aliases(item):
                state = 'incompatible' if item.get('schema_incompatible') else status
                old_state, old = index.get(alias, ('', {}))
                if old_state == 'conflict' or (old_state in ('existing', 'incompatible') and old.get('id') != item['id']):
                    index[alias] = ('conflict', {})
                else:
                    index[alias] = (state, item)
    return index


def local_match(index, record):
    matches = [index[alias] for alias in aliases(record) if alias in index]
    if any(state == 'conflict' for state, _ in matches) or len({item.get('id') for _, item in matches}) > 1:
        return ('conflict', {})
    return matches[0] if matches else ('new', {})


def response_row(row):
    return {key: value for key, value in row.items() if key != 'record'}


async def preview(body, index, members, root):
    tokens = split_input(body.text)
    requested = [identify(token) for token in tokens]
    reusable = {}
    if body.retry_preview_id:
        with _cache_lock:
            saved = _cache.get(body.retry_preview_id)
            if saved and saved[0] >= time.monotonic() and saved[1:3] == (root, body.library_id):
                for row in saved[3]:
                    if row.get('record') and row['status'] == 'new':
                        reusable[identify(row['input'])] = row['record']
    slots = asyncio.Semaphore(3)

    async def lookup(identifier):
        kind, value = identifier
        if identifier in index:
            local = index[identifier][1]
            return {'doi': local.get('doi', value if kind == 'doi' else ''), 'pmid': local.get('pmid', value if kind == 'pmid' else ''),
                    'title': local.get('title', '')}, ''
        if identifier in reusable:
            return reusable[identifier], ''
        async with slots:
            try:
                fetch = fetch_crossref(value) if kind == 'doi' else fetch_pubmed(value)
                record = await asyncio.wait_for(fetch, timeout=25)
                if (not record.get('title') or (kind, value) not in aliases(record)):
                    raise LookupError_('返回的编号或题名与请求不匹配')
                return record, ''
            except asyncio.TimeoutError:
                return {}, '查询超时，可重新预览'
            except (LookupError_, UnsafeURLError, ValueError, TypeError, KeyError, AttributeError) as exc:
                return {}, str(exc)

    distinct = list(dict.fromkeys(i for i in requested if i))
    looked_up = dict(zip(distinct, await asyncio.gather(*(lookup(i) for i in distinct))))
    rows, seen = [], {}
    for pos, (token, identifier) in enumerate(zip(tokens, requested)):
        row = {'key': pos, 'input': token, 'status': 'invalid', 'paper_id': '', 'title': '', 'doi': '', 'pmid': '', 'in_project': False}
        rows.append(row)
        if not identifier:
            continue
        record, error = looked_up[identifier]
        if error:
            row.update(status='failed', reason=error)
            continue
        record = dict(record)
        status, local = local_match(index, record)
        row.update(record=record, status=status, title=record.get('title') or '', doi=record.get('doi') or '', pmid=record.get('pmid') or '',
                   paper_id=local.get('id', ''), in_project=local.get('id') in members)
        previous = next((seen[a] for a in aliases(record) if a in seen), None)
        if previous is not None:
            row.update(status='duplicate', duplicate_of=previous['key'])
            if row['pmid'] and not previous['pmid']:
                previous['pmid'] = previous['record']['pmid'] = row['pmid']
            continue
        for alias in aliases(record):
            seen[alias] = row
    return rows


def remember(root, library_id, rows):
    token = uuid.uuid4().hex
    with _cache_lock:
        now = time.monotonic()
        for key in [k for k, item in _cache.items() if item[0] < now]:
            del _cache[key]
        if len(_cache) >= 16:
            _cache.popitem(last=False)
        _cache[token] = (now + 900, root, library_id, rows)
    return {'preview_id': token, 'library_id': library_id, 'rows': [response_row(row) for row in rows]}


def selection(root, body):
    with _cache_lock:
        saved = _cache.get(body.preview_id)
        if not saved or saved[0] < time.monotonic() or saved[1] != root:
            raise HTTPException(409, '预览已过期，请重新预览')
        _, _, library_id, rows = saved
        if len(set(body.keys)) != len(body.keys) or any(k < 0 or k >= len(rows) or rows[k]['status'] not in ('new', 'existing') for k in body.keys):
            raise HTTPException(422, '请选择可加入的预览条目')
        return library_id, [rows[k] for k in body.keys]
