"""Project-scoped PubMed searches, refreshed on demand."""
import hashlib
import json
import re
from urllib.parse import urlencode

from fastapi import HTTPException
from pydantic import BaseModel, Field

from external import _get_json, LookupError_
from reading_workflow import atomic_json
from llm import UnsafeURLError

HOST = 'eutils.ncbi.nlm.nih.gov'
WINDOW = 50


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=1000)


class SeenIn(BaseModel):
    result_id: str = Field(pattern=r'^[0-9a-f]{64}$')


def directory(root, scope):
    if scope != 'all' and not re.fullmatch('[0-9a-f]{32}', scope):
        raise HTTPException(404, '未找到课题库')
    return root / 'pubmed-searches' / scope


def path_for(root, scope, key):
    if not re.fullmatch('[0-9a-f]{32}', key):
        raise HTTPException(404, '检索式不存在')
    return directory(root, scope) / (key + '.json')


def read(path):
    if not path.exists():
        raise HTTPException(404, '检索式不存在')
    try:
        record = json.loads(path.read_text('utf-8'))
        if (record['version'] != 1 or record['id'] != path.stem or not isinstance(record['query'], str)
                or not isinstance(record['seen'], list) or not isinstance(record['latest'], list)):
            raise ValueError()
        return record
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise HTTPException(409, '检索记录无法读取') from exc


def view(record):
    seen = set(record['seen'])
    return {k: record[k] for k in ('id', 'query', 'total', 'result_id')} | {
        'unseen': [pmid for pmid in record['latest'] if pmid not in seen], 'window': WINDOW}


def create(root, scope, query):
    query = query.strip()
    if not query:
        raise HTTPException(422, '请填写 PubMed 检索式')
    key = hashlib.sha256(query.encode()).hexdigest()[:32]
    path = path_for(root, scope, key)
    if path.exists():
        return view(read(path))
    record = dict(version=1, id=key, query=query, seen=[], latest=[], total=None, result_id='')
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, record)
    return view(record)


async def search(query):
    params = urlencode(dict(db='pubmed', retmode='json', sort='pub_date', retmax=WINDOW, term=query))
    try:
        data = await _get_json(f'https://{HOST}/entrez/eutils/esearch.fcgi?{params}', HOST)
        result = data['esearchresult']
        if result.get('ERROR') or any((result.get('errorlist') or {}).values()):
            raise HTTPException(422, 'PubMed 无法按该检索式查询，请核对字段或关键词')
        ids = result['idlist']
        count = int(result['count'])
        if not isinstance(ids, list) or count < 0 or any(not isinstance(pid, str) or not re.fullmatch(r'[1-9][0-9]{0,8}', pid) for pid in ids):
            raise ValueError()
        return count, list(dict.fromkeys(ids))
    except (KeyError, TypeError, ValueError, LookupError_, UnsafeURLError) as exc:
        raise HTTPException(502, 'PubMed 查询暂未成功，请重试') from exc


def record_result(path, total, pmids):
    record = read(path)
    record.update(latest=pmids, total=total, result_id=hashlib.sha256(','.join(pmids).encode()).hexdigest())
    atomic_json(path, record)
    return view(record)


def mark_seen(path, result_id):
    record = read(path)
    if record['result_id'] != result_id:
        raise HTTPException(409, '检索结果已更新，请刷新后操作')
    record['seen'] = list(dict.fromkeys(record['seen'] + record['latest']))
    atomic_json(path, record)
    return view(record)
