"""Project-scoped PubMed tracking with complete creation-date windows and local review pages."""
import asyncio
import hashlib
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import HTTPException
from pydantic import BaseModel, Field

from external import _get_json, LookupError_
from reading_workflow import atomic_json
from llm import UnsafeURLError

HOST = 'eutils.ncbi.nlm.nih.gov'
WINDOW = 50
SEARCH_LIMIT = 10_000
MAX_PMID = 999_999_999
INITIAL_DAYS = 7
in_flight = set()  # Managed by the API's storage lock.
_rate_lock = threading.Lock()
_next_call = 0.0


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=1000)


class SeenIn(BaseModel):
    result_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    page: int = Field(default=1, ge=1, strict=True)


class TrackingIn(BaseModel):
    enabled: bool = Field(strict=True)


def directory(root, scope):
    if scope != 'all' and not re.fullmatch('[0-9a-f]{32}', scope):
        raise HTTPException(404, '未找到课题库')
    return root / 'pubmed-searches' / scope


def path_for(root, scope, key):
    if not re.fullmatch('[0-9a-f]{32}', key):
        raise HTTPException(404, '检索式不存在')
    return directory(root, scope) / (key + '.json')


def now():
    return datetime.now(timezone.utc)


def valid_ids(ids):
    return isinstance(ids, list) and all(isinstance(pid, str) and re.fullmatch(r'[1-9][0-9]{0,8}', pid) for pid in ids)


def read(path):
    if not path.exists():
        raise HTTPException(404, '检索式不存在')
    try:
        record = json.loads(path.read_text('utf-8'))
        if (record['version'] not in (1, 2) or record['id'] != path.stem or not isinstance(record['query'], str)
                or not valid_ids(record['seen']) or not valid_ids(record['latest'])
                or not (record['total'] is None or type(record['total']) is int and record['total'] >= 0)):
            raise ValueError()
        for key in ('last_checked', 'last_attempt'):
            if record.get(key) and datetime.fromisoformat(record[key]).tzinfo is None:
                raise ValueError()
        if type(record.get('tracking', True)) is not bool:
            raise ValueError()
        return record
    except (ValueError, OSError, KeyError, AttributeError, TypeError) as exc:
        raise HTTPException(409, '检索记录无法读取') from exc


def pending(record):
    seen = set(record['seen'])
    return list(dict.fromkeys(pid for pid in record['latest'] if pid not in seen))


def view(record, page=1):
    backlog = pending(record)
    pages = max(1, (len(backlog) + WINDOW - 1) // WINDOW)
    page = min(page, pages)
    batch = backlog[(page - 1)*WINDOW:page*WINDOW]
    token = hashlib.sha256(','.join(batch).encode()).hexdigest()
    return {'id': record['id'], 'query': record['query'], 'total': record['total'], 'window': WINDOW,
            'unseen': batch, 'unseen_total': len(backlog), 'result_id': token, 'page': page, 'pages': pages,
            'last_checked': record.get('last_checked'), 'last_error': record.get('last_error', ''),
            'tracking': record.get('tracking', True)}


def write(path, record):
    record['version'] = 2
    atomic_json(path, record)


def create(root, scope, query):
    query = query.strip()
    if not query:
        raise HTTPException(422, '请填写 PubMed 检索式')
    key = hashlib.sha256(query.encode()).hexdigest()[:32]
    path = path_for(root, scope, key)
    if path.exists():
        return view(read(path))
    record = dict(version=2, id=key, query=query, seen=[], latest=[], total=None)
    path.parent.mkdir(parents=True, exist_ok=True)
    write(path, record)
    return view(record)


def due(record, stamp):
    if not record.get('tracking', True):
        return False
    success = record.get('last_checked')
    attempt = record.get('last_attempt')
    return ((not success or stamp - datetime.fromisoformat(success) >= timedelta(days=1))
            and (not attempt or stamp - datetime.fromisoformat(attempt) >= timedelta(minutes=15)))


def search_dates(record, stamp):
    # Revisit the previous calendar day to cover partially indexed days/time zones.
    previous = record.get('last_checked')
    start = (datetime.fromisoformat(previous).date() - timedelta(days=1) if previous
             else stamp.date() - timedelta(days=INITIAL_DAYS - 1))
    return min(start, stamp.date()), stamp.date()


async def pace_requests():
    global _next_call
    with _rate_lock:
        current = time.monotonic()
        start = max(current, _next_call)
        _next_call = start + 0.35
    if start > current:
        await asyncio.sleep(start - current)


async def fetch_shard(term):
    params = urlencode(dict(db='pubmed', retmode='json', sort='pub_date', retmax=SEARCH_LIMIT, term=term))
    await pace_requests()
    try:
        data = await _get_json(f'https://{HOST}/entrez/eutils/esearch.fcgi?{params}', HOST)
        result = data['esearchresult']
        if result.get('ERROR') or any((result.get('errorlist') or {}).values()):
            raise HTTPException(422, 'PubMed 无法按该检索式查询，请核对字段或关键词')
        ids = result['idlist']
        count = int(result['count'])
        if count < 0 or not valid_ids(ids) or (count <= SEARCH_LIMIT and len(set(ids)) != count):
            raise ValueError()
        return count, ids
    except (KeyError, TypeError, ValueError, LookupError_, UnsafeURLError) as exc:
        raise HTTPException(502, 'PubMed 查询暂未成功，请重试') from exc


async def search(query, start, end):
    # A shard is accepted only when all its IDs were returned in one response;
    # no changing remote offsets or expiring history tokens are persisted.
    async def collect(first, last, lo=1, hi=MAX_PMID):
        dates = f'{first:%Y/%m/%d}:{last:%Y/%m/%d}[crdt]'
        term = f'({query}) AND {dates}'
        if lo != 1 or hi != MAX_PMID:
            term += f' AND {lo}:{hi}[uid]'
        count, ids = await fetch_shard(term)
        if count <= SEARCH_LIMIT:
            return ids
        if first < last:
            middle = first + timedelta(days=(last - first).days // 2)
            return await collect(middle + timedelta(days=1), last) + await collect(first, middle)
        if lo >= hi:
            raise HTTPException(502, 'PubMed 查询暂未成功，请重试')
        pivot = (lo + hi) // 2
        return await collect(first, last, pivot + 1, hi) + await collect(first, last, lo, pivot)

    ids = list(dict.fromkeys(await collect(start, end)))
    return len(ids), ids


def record_result(path, total, pmids, stamp):
    # Read again so that reviewing a page mid-request never resets its seen IDs.
    record = read(path)
    previous = pending(record)
    known = set(previous) | set(record['seen'])
    fresh = [pid for pid in dict.fromkeys(pmids) if pid not in known]
    record.update(latest=fresh + previous, total=total, last_checked=stamp.isoformat(),
                  last_attempt=stamp.isoformat(), last_error='')
    write(path, record)
    return view(record)


def mark_seen(path, result_id, page):
    record = read(path)
    shown = view(record, page)
    if shown['result_id'] != result_id or shown['page'] != page:
        raise HTTPException(409, '检索结果已更新，请刷新后操作')
    seen = set(shown['unseen'])
    record['seen'] = list(dict.fromkeys(record['seen'] + shown['unseen']))
    record['latest'] = [pid for pid in pending(record) if pid not in seen]
    write(path, record)
    return view(record, page)
