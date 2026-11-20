"""Discover and retrieve files declared by the official NLM PMC article datasets."""
import asyncio
import hashlib
import io
import json
import re
import time
import zipfile
from collections import OrderedDict
from pathlib import PurePosixPath
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlsplit
import xml.etree.ElementTree as ET

import httpx

from external import USER_AGENT
from llm import UnsafeURLError, assert_safe_external_url

BUCKET = 'pmc-oa-opendata'
HOST = BUCKET + '.s3.amazonaws.com'
BASE = 'https://' + HOST
CONVERTER_HOST = 'pmc.ncbi.nlm.nih.gov'
METADATA_LIMIT = 2 * 1024 * 1024
ATTACHMENT_LIMIT = 80 * 1024 * 1024
MIMES = {
    '.pdf': 'application/pdf', '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.xls': 'application/vnd.ms-excel', '.csv': 'text/csv', '.tsv': 'text/tab-separated-values',
    '.zip': 'application/zip', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
}
_pmcid = re.compile(r'PMC[0-9]+')
_preview = OrderedDict()


class PMCError(ValueError):
    pass


async def _fetch(url, host, limit):
    """Allow only redirects within the same fixed official host; cap decoded bytes."""
    async def read():
        async with httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(16.0, connect=8.0),
                                     headers={'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity'}) as client:
            current = url
            for _ in range(4):
                parsed = urlsplit(current)
                if parsed.scheme != 'https' or parsed.netloc != host:
                    raise PMCError('PMC 附件地址无效')
                await asyncio.to_thread(assert_safe_external_url, current)
                async with client.stream('GET', current) as response:
                    if response.is_redirect and response.headers.get('location'):
                        current = urljoin(current, response.headers['location'])
                        continue
                    if response.status_code == 404:
                        return None
                    if response.status_code != 200:
                        raise PMCError(f'PMC HTTP {response.status_code}')
                    length = response.headers.get('content-length', '')
                    if length.isdecimal() and int(length) > limit:
                        raise PMCError('PMC 文件超过大小上限')
                    data = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        if len(data) + len(chunk) > limit:
                            raise PMCError('PMC 文件超过大小上限')
                        data.extend(chunk)
                    return bytes(data)
            raise PMCError('PMC 附件重定向次数过多')
    try:
        return await asyncio.wait_for(read(), timeout=60)
    except (httpx.HTTPError, asyncio.TimeoutError, UnsafeURLError) as exc:
        raise PMCError('PMC 连接失败，请稍后重试') from exc


def identity(meta):
    return (str(meta.get('pmcid') or '').upper().strip(), str(meta.get('doi') or '').strip().casefold(),
            str(meta.get('pmid') or '').strip())


async def article_manifests(meta):
    """Return OA version manifests that match the paper's supplied identifiers."""
    pmcid, doi, pmid = identity(meta)
    if not _pmcid.fullmatch(pmcid):
        ident = doi or pmid
        if not ident:
            return []
        url = 'https://' + CONVERTER_HOST + '/tools/idconv/api/v1/articles/?' + urlencode(
            {'ids': ident, 'format': 'json', 'tool': 'BioReader'})
        data = await _fetch(url, CONVERTER_HOST, METADATA_LIMIT)
        if not data:
            return []
        try:
            conversion = json.loads(data)
            if conversion.get('status') != 'ok':
                raise PMCError('PMC 编号查询失败')
            records = conversion.get('records', [])
            pmcid = next((r['pmcid'] for r in records if isinstance(r, dict) and _pmcid.fullmatch(str(r.get('pmcid', '')))
                          and str(r.get('doi') if doi else r.get('pmid') or '').casefold() == ident), '')
        except (KeyError, TypeError, AttributeError, UnicodeError, json.JSONDecodeError) as exc:
            raise PMCError('PMC 编号响应无效') from exc
    if not pmcid:
        return []
    listing = BASE + '/?' + urlencode({'list-type': 2, 'prefix': 'metadata/' + pmcid + '.', 'max-keys': 100})
    data = await _fetch(listing, HOST, METADATA_LIMIT)
    if not data:
        return []
    try:
        root = ET.fromstring(data)
        versions = []
        for node in root.findall('.//{*}Contents/{*}Key'):
            match = re.fullmatch(r'metadata/' + pmcid + r'\.([1-9][0-9]*)\.json', node.text or '')
            if match:
                versions.append(int(match[1]))
        result = []
        for version in sorted(set(versions), reverse=True)[:10]:
            data = await _fetch(f'{BASE}/metadata/{pmcid}.{version}.json', HOST, METADATA_LIMIT)
            if not data:
                continue
            obj = json.loads(data)
            if obj.get('pmcid') != pmcid or obj.get('version') != version or obj.get('is_pmc_openaccess') is not True:
                continue
            if doi and str(obj.get('doi') or '').casefold() != doi:
                continue
            if pmid and str(obj.get('pmid') or '') != pmid:
                continue
            result.append(obj)
        return sorted(result, key=lambda item: (bool(item.get('is_manuscript')), -item['version']))
    except (ValueError, ET.ParseError, AttributeError, TypeError, UnicodeError) as exc:
        if isinstance(exc, PMCError):
            raise
        raise PMCError('PMC 文件清单格式无效') from exc


def object_location(uri, version_prefix):
    """Only a single, version-local object may be selected from a manifest."""
    if not isinstance(uri, str) or not re.fullmatch(r'PMC[0-9]+\.[1-9][0-9]*', version_prefix):
        return None
    parsed = urlsplit(uri)
    prefix = '/' + version_prefix + '/'
    if parsed.scheme != 's3' or parsed.netloc != BUCKET or not parsed.path.startswith(prefix):
        return None
    name = unquote(parsed.path[len(prefix):])
    checksum = parse_qs(parsed.query).get('md5', [''])[0].lower()
    if (not name or name in {'.', '..'} or len(name) > 255 or '/' in name or '\\' in name
            or any(ord(c) < 32 for c in name) or not re.fullmatch(r'[0-9a-f]{32}', checksum)):
        return None
    return BASE + prefix + quote(name, safe=''), checksum


async def checked_object(url, md5, limit):
    content = await _fetch(url, HOST, limit)
    if content is None or hashlib.md5(content, usedforsecurity=False).hexdigest() != md5:
        raise PMCError('PMC 附件已变化，请重新查找')
    return content


def parse_supplements(xml, manifest):
    prefix = f"{manifest['pmcid']}.{manifest['version']}"
    media = {}
    for uri in manifest.get('media_urls') or []:
        location = object_location(uri, prefix)
        if location:
            media[unquote(urlsplit(location[0]).path.split('/')[-1])] = location
    root = ET.fromstring(xml)
    items = {}
    locator = '{http://www.w3.org/1999/xlink}href'
    for element in root.iter():
        if element.tag.rsplit('}', 1)[-1] not in ('supplementary-material', 'inline-supplementary-material'):
            continue
        caption = element.find('{*}caption')
        label = element.find('{*}label')
        title = ' '.join(' '.join(' '.join(n.itertext()) for n in [label, caption] if n is not None).split())[:400]
        for child in element.iter():
            ref = child.get(locator) or ''
            name = PurePosixPath(unquote(urlsplit(ref).path)).name
            ext = PurePosixPath(name).suffix.lower()
            if name not in media or ext not in MIMES:
                continue
            url, checksum = media[name]
            key = hashlib.sha256(f"{manifest['pmcid']}|{name}|{checksum}".encode()).hexdigest()
            items[key] = {'id': key, 'filename': name, 'label': title or name, 'mime': MIMES[ext],
                          'source_url': url, 'md5': checksum, 'article_version': prefix}
    return list(items.values())


async def discover(meta):
    files = {}
    for item in await article_manifests(meta):
        if not item.get('media_urls'):
            continue
        prefix = f"{item['pmcid']}.{item['version']}"
        location = object_location(item.get('xml_url'), prefix)
        if location:
            xml = await checked_object(*location, limit=15 * 1024 * 1024)
            try:
                for row in parse_supplements(xml, item):
                    files.setdefault(row['id'], row)
            except ET.ParseError as exc:
                raise PMCError('PMC 文章 XML 格式无效') from exc
    return list(files.values())


def cache_candidates(pid, meta, items):
    _preview[pid] = (time.monotonic(), identity(meta), {row['id']: row for row in items})
    _preview.move_to_end(pid)
    while len(_preview) > 64:
        _preview.popitem(last=False)


def candidate(pid, meta, key):
    entry = _preview.get(pid)
    if entry and entry[1] == identity(meta) and time.monotonic() - entry[0] < 1800:
        return entry[2].get(key)
    return None


def validate_file(filename, content):
    ext = PurePosixPath(filename).suffix.lower()
    valid = False
    if ext == '.pdf':
        valid = content.startswith(b'%PDF-')
    elif ext in ('.xlsx', '.zip', '.docx'):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if ext == '.zip':
                    valid = True
                else:
                    required = 'xl/workbook.xml' if ext == '.xlsx' else 'word/document.xml'
                    valid = required in archive.namelist() and '[Content_Types].xml' in archive.namelist()
        except (OSError, ValueError, zipfile.BadZipFile):
            pass
    elif ext == '.xls':
        valid = content.startswith(bytes.fromhex('d0cf11e0a1b11ae1'))
    elif ext in ('.csv', '.tsv'):
        sample = content[:4096].lstrip(b'\xef\xbb\xbf \r\n\t').lower()
        valid = bool(sample) and b'\x00' not in sample and not sample.startswith((b'<html', b'<!doctype', b'<script', b'<?xml'))
    if not valid:
        raise PMCError('补充附件内容与文件类型不符')
