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

import article_links
from external import DOI_RE, LookupError_, _get_json, _strip_jats, fetch_crossref
from llm import UnsafeURLError

MAX_ITEMS = 50
PREVIEW_TTL = 15 * 60
PMC_HOST = 'www.ebi.ac.uk'
META_FIELDS = ('title', 'doi', 'pmid', 'pmcid', 'authors', 'journal', 'year', 'abstract', 'url', 'type')
IDENTIFIER_HOSTS = {'doi.org', 'dx.doi.org', 'pubmed.ncbi.nlm.nih.gov', 'www.ncbi.nlm.nih.gov',
                    'ncbi.nlm.nih.gov', 'europepmc.org', 'www.europepmc.org'}
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
    tokens = []
    for item in re.split(r'[\s，；]+', text.strip()):
        # Commas belong to URL paths and query values. Preserve the pasted target.
        if item.lower().startswith(('http://', 'https://')):
            tokens.append(item)
        else:
            tokens.extend(part for part in item.split(',') if part)
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
    result = [(kind, str(record[kind]).strip().lower()) for kind in ('doi', 'pmid') if record.get(kind)]
    link = record.get('_link') or {}
    source = record.get('fulltext_source') or {}
    for value in (record.get('import_url'), record.get('article_url'), link.get('article_url'),
                  link.get('pdf_url'), source.get('url')):
        if value:
            try:
                result.append(('url', article_links.public_url(value)))
            except (TypeError, ValueError):
                continue
    main_sha = (record.get('document_revisions') or {}).get('main')
    prior_sha = record.get('import_pdf_sha256') if not record.get('has_file') and not main_sha else ''
    for digest in (link.get('sha256'), main_sha, prior_sha):
        if isinstance(digest, str) and re.fullmatch(r'[a-fA-F0-9]{64}', digest):
            result.append(('sha256', digest.lower()))
    return list(dict.fromkeys(result))


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


def preview_aliases(record):
    # Only cached previews carry extra identities from duplicate inputs in the same batch.
    return set(aliases(record)).union(record.get('_match_aliases', ()))


def local_match(index, record):
    matches = [index[alias] for alias in preview_aliases(record) if alias in index]
    if any(state == 'conflict' for state, _ in matches) or len({item.get('id') for _, item in matches}) > 1:
        return ('conflict', {})
    if not matches:
        return ('new', {})
    state, local = matches[0]
    for field in ('doi', 'pmid'):
        if (record.get(field) and local.get(field)
                and str(record[field]).strip().lower() != str(local[field]).strip().lower()):
            return ('conflict', {})
    return state, local


def response_row(row):
    return {key: value for key, value in row.items() if key != 'record'}


def request_key(token):
    if token.lower().startswith(('https://', 'http://')):
        try:
            return 'url', article_links.public_url(token)
        except ValueError:
            return None
    return identify(token)


def _metadata_from_local(index, key):
    kind, value = key
    item = index[key][1]
    record = {field: item.get(field, '') for field in ('doi', 'pmid', 'title')}
    if kind in ('doi', 'pmid'):
        record[kind] = record[kind] or value
    return record


def check_url_identifier(identifier, record):
    if not identifier:
        return
    kind, original = identifier
    actual = str(record.get(kind) or '').strip().lower()
    if not actual:
        return
    matches = article_links.doi_path_matches(actual, original) if kind == 'doi' else actual == original
    if not matches:
        raise article_links.ArticleIdentityError('返回的编号或题名与请求不匹配')


def _combine_records(rows, index, members):
    # Group by every observed identity so a PMID+DOI row can bridge two earlier inputs.
    groups = []
    for row in rows:
        if 'record' not in row:
            continue
        keys = preview_aliases(row['record'])
        matching = [group for group in groups if group[0] & keys]
        grouped = [row]
        for group in matching:
            groups.remove(group)
            keys.update(group[0])
            grouped.extend(group[1])
        groups.append((keys, grouped))
    for keys, group in groups:
        group.sort(key=lambda row: row['key'])
        first = group[0]
        record = dict(first['record'])
        links = [r['record'] for r in group if r['record'].get('_link')]
        if links:
            # Prefer the link that can get the actual checked PDF.
            best = max(links, key=lambda r: (bool(r['_link'].get('sha256')), bool(r['_link'].get('pdf_url'))))
            record.update({k: best[k] for k in ('_link', 'import_url', 'article_url')})
        for row in group[1:]:
            for field in META_FIELDS:
                if not record.get(field) and row['record'].get(field):
                    record[field] = row['record'][field]
            row.update(status='duplicate', duplicate_of=first['key'])
        # The chosen link drives retrieval. Every observed identity must still be
        # considered when rechecking storage after a concurrent import or deletion.
        record['_match_aliases'] = sorted(keys.difference(aliases(record)))
        status, local = local_match(index, record)
        if any(len({value for kind, value in keys if kind == identifier}) > 1 for identifier in ('doi', 'pmid')):
            status, local = 'conflict', {}
        first.update(record=record, status=status, is_link=bool(links), paper_id=local.get('id', ''),
                     in_project=local.get('id') in members, has_file=bool(local.get('has_file')),
                     title=record.get('title', ''), doi=record.get('doi', ''), pmid=record.get('pmid', ''))


async def preview(body, index, members, root):
    tokens = split_input(body.text)
    requested = [request_key(token) for token in tokens]
    reusable = {}
    if body.retry_preview_id:
        with _cache_lock:
            saved = _cache.get(body.retry_preview_id)
            if (saved and saved[0] >= time.monotonic() and saved[1:3] == (root, body.library_id)
                    and [r['input'] for r in saved[3]] == tokens):
                for row in saved[3]:
                    if row.get('record') and row['status'] in ('new', 'duplicate'):
                        reusable[request_key(row['input'])] = row['record']
    slots = asyncio.Semaphore(3)
    lookups = {}

    async def resolve_identifier(key):
        kind, value = key
        if key in index:
            return _metadata_from_local(index, key)
        async with slots:
            lookup = fetch_crossref if kind == 'doi' else fetch_pubmed
            record = await asyncio.wait_for(lookup(value), timeout=25)
            if not record.get('title') or key not in aliases(record):
                raise ValueError('返回的编号或题名与请求不匹配')
            return record

    async def metadata(key):
        if key not in lookups:
            lookups[key] = asyncio.create_task(resolve_identifier(key))
        return await lookups[key]

    async def inspect_link(url):
        identifier = identify(url)
        observed = {}
        identity_host = urlsplit(url).hostname in IDENTIFIER_HOSTS
        if ('url', url) in index:
            observed = _metadata_from_local(index, ('url', url))
        elif not (identifier and identity_host):
            try:
                async with slots:
                    observed = await asyncio.wait_for(article_links.inspect_url(url), timeout=100)
            except article_links.ArticleIdentityError:
                raise
            except (article_links.ArticleLinkError, LookupError_, UnsafeURLError, asyncio.TimeoutError):
                if not identifier:
                    raise
        check_url_identifier(identifier, observed)
        found = next((key for key in aliases(observed) if key[0] in ('doi', 'pmid')), identifier)
        record = {key: observed[key] for key in META_FIELDS if isinstance(observed.get(key), str)}
        if found:
            try:
                resolved = await metadata(found)
                check_url_identifier(identifier, resolved)
                for key in ('doi', 'pmid'):
                    if resolved.get(key) and observed.get(key) and str(resolved[key]).lower() != str(observed[key]).lower():
                        raise ValueError('返回的编号或题名与请求不匹配')
                record.update({k: resolved[k] for k in META_FIELDS if isinstance(resolved.get(k), str) and resolved[k]})
            except (LookupError_, UnsafeURLError, asyncio.TimeoutError):
                if not observed.get('title'):
                    raise
        if not record.get('title') and not (('url', url) in index or found in index):
            raise LookupError_('返回的编号或题名与请求不匹配')
        hint = observed.get('_link') or {}
        link = {key: hint[key] for key in ('article_url', 'pdf_url', 'sha256') if isinstance(hint.get(key), str) and hint[key]}
        link.setdefault('article_url', url)
        if hint.get('inspected') is True:
            link['inspected'] = True
        record.update(import_url=url, article_url=link['article_url'], _link=link)
        return record

    async def lookup(key):
        try:
            if key in reusable:
                return reusable[key], ''
            record = await inspect_link(key[1]) if key[0] == 'url' else await metadata(key)
            return record, ''
        except asyncio.TimeoutError:
            return {}, '查询超时，可重新预览'
        except (LookupError_, UnsafeURLError, ValueError, TypeError, KeyError, AttributeError) as exc:
            return {}, str(exc)

    distinct = list(dict.fromkeys(i for i in requested if i))
    looked_up = dict(zip(distinct, await asyncio.gather(*(lookup(i) for i in distinct))))
    rows = []
    for pos, (token, key) in enumerate(zip(tokens, requested)):
        row = {'key': pos, 'input': token, 'status': 'invalid', 'paper_id': '', 'title': '', 'doi': '', 'pmid': '',
               'in_project': False, 'is_link': bool(key and key[0] == 'url'), 'has_file': False}
        rows.append(row)
        if key:
            record, error = looked_up[key]
            if error:
                row.update(status='failed', reason=error)
            else:
                row.update(record=record, title=record.get('title', ''), doi=record.get('doi', ''), pmid=record.get('pmid', ''))
    _combine_records(rows, index, members)
    return rows


def remember(root, library_id, rows):
    token = uuid.uuid4().hex
    with _cache_lock:
        now = time.monotonic()
        for key in [k for k, item in _cache.items() if item[0] < now]:
            del _cache[key]
        if len(_cache) >= 16:
            _cache.popitem(last=False)
        _cache[token] = (now + PREVIEW_TTL, root, library_id, rows)
    return {'preview_id': token, 'library_id': library_id, 'rows': [response_row(row) for row in rows]}


def selection(root, body):
    with _cache_lock:
        now = time.monotonic()
        saved = _cache.get(body.preview_id)
        if not saved or saved[0] < now or saved[1] != root:
            raise HTTPException(409, '预览已过期，请重新预览')
        _, _, library_id, rows = saved
        if len(set(body.keys)) != len(body.keys) or any(k < 0 or k >= len(rows) or rows[k]['status'] not in ('new', 'existing') for k in body.keys):
            raise HTTPException(422, '请选择可加入的预览条目')
        # A confirmed queue issues separate requests for links. Expire inactive
        # previews but keep a progressing queue alive even across slow downloads.
        _cache[body.preview_id] = (now + PREVIEW_TTL, root, library_id, rows)
        _cache.move_to_end(body.preview_id)
        return library_id, [rows[k] for k in body.keys]
