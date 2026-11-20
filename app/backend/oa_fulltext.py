"""Obtain matching primary PDFs from publication pages and public literature indexes."""
import asyncio
import hashlib
import io
import json
import os
import re
import shutil
import unicodedata
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit

import httpx
from pypdf import PdfReader

from external import DOI_RE, USER_AGENT
from llm import UnsafeURLError, assert_safe_external_url
import pmc_materials

INDEX_HOSTS = {'www.ebi.ac.uk', 'api.openalex.org'}
PDF_DOMAINS = {'europepmc.org', 'pmc.ncbi.nlm.nih.gov', 'cdn.ncbi.nlm.nih.gov',
               'pmc-oa-opendata.s3.amazonaws.com', 'nature.com', 'springer.com', 'biomedcentral.com',
               'frontiersin.org', 'plos.org', 'elifesciences.org', 'biorxiv.org', 'medrxiv.org',
               'arxiv.org', 'oup.com', 'wiley.com', 'onlinelibrary.wiley.com', 'mdpi.com', 'mdpi-res.com',
               'sciencedirect.com', 'cell.com', 'thelancet.com', 'jamanetwork.com', 'bmj.com',
               'rupress.org', 'ashpublications.org', 'pnas.org', 'aacrjournals.org', 'elsevier.com'}
LANDING_DOMAINS = PDF_DOMAINS | {'doi.org'}
SUPPLEMENT_PATH = re.compile(r'(?:^|[/_.-])(?:supp(?:l(?:ement(?:ary|al)?)?)?|supporting|additional|esm|mmc[0-9]+)'
                             r'(?:[_. /-]|[0-9]|$)', re.I)
SUPPLEMENT_HEADING = re.compile(
    r'^\s*(?:(?:electronic\s+)?(?:supplementary|supplemental|supporting)\s+'
    r'(?:information|materials?|methods?|appendix|files?|figures?|tables?|data)(?:\b|$)'
    r'|(?:fig(?:ure)?\.?|table)\s+s\d+\s*[.:|])', re.I | re.M)


class SourceError(RuntimeError):
    pass


class ReviewNeeded(SourceError):
    pass


class CitationTags(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pdf = ''
        self.doi = ''

    def handle_starttag(self, tag, attrs):
        if tag == 'meta':
            fields = dict(attrs)
            name = (fields.get('name') or '').lower()
            if name == 'citation_pdf_url' and not self.pdf:
                self.pdf = fields.get('content') or ''
            elif name == 'citation_doi':
                self.doi = fields.get('content') or ''


def permitted(url, domains):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == 'https' and parsed.hostname and parsed.port in (None, 443)
                and parsed.username is None and parsed.password is None
                and any(parsed.hostname == host or parsed.hostname.endswith('.' + host) for host in domains))
    except (ValueError, TypeError):
        return False


async def read_url(client, url, domains, byte_limit):
    for _ in range(6):
        if not permitted(url, domains):
            raise SourceError('来源地址超出支持范围，可在浏览器中打开原文。')
        try:
            await asyncio.to_thread(assert_safe_external_url, url)
            req = client.build_request('GET', url, headers={'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity'})
            req.headers.pop('cookie', None)
            response = await client.send(req, stream=True, follow_redirects=False)
            try:
                if response.status_code in (301, 302, 303, 307, 308):
                    target = response.headers.get('location')
                    if not target:
                        raise SourceError('来源未提供重定向目标。')
                    url = urljoin(url, target)
                    continue
                if response.status_code != 200:
                    raise SourceError(f'{urlsplit(url).hostname} 返回 HTTP {response.status_code}')
                length = response.headers.get('content-length', '')
                if length.isdigit() and int(length) > byte_limit:
                    raise SourceError('来源文件超过大小限制。')
                content = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    if len(content) + len(chunk) > byte_limit:
                        raise SourceError('来源文件超过大小限制。')
                    content.extend(chunk)
                return str(response.url), bytes(content)
            finally:
                await response.aclose()
        except (httpx.HTTPError, UnsafeURLError) as exc:
            raise SourceError(f'{urlsplit(url).hostname} 连接失败（{type(exc).__name__}）。') from exc
    raise SourceError('来源重定向次数过多。')


def doi_of(value):
    text = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', str(value or '').strip(), flags=re.I)
    return text.lower() if DOI_RE.fullmatch(text) else ''


def identity(meta):
    return (doi_of(meta.get('doi')), str(meta.get('pmid') or '').strip(),
            str(meta.get('pmcid') or '').strip().upper(), str(meta.get('title') or '').strip())


def normalized_title(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value or '').casefold() if c.isalnum())


def verify_pdf(content, meta):
    if not content.startswith(b'%PDF-'):
        raise SourceError('下载地址没有返回 PDF。')
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 600:
            raise SourceError('PDF 已加密或超出 1–600 页范围。')
        info = reader.metadata or {}
        page = (reader.pages[0].extract_text() or '')[:12000]
    except SourceError:
        raise
    except Exception as exc:
        raise SourceError('下载的 PDF 无法读取。') from exc
    # References on the title page cannot establish the work's own identity.
    opening = re.split(r'^\s*(?:References|Bibliography)\s*$', page, flags=re.I | re.M)[0][:7000]
    title = str(info.get('/Title') or '')
    if SUPPLEMENT_HEADING.search(opening[:1400]) or SUPPLEMENT_HEADING.search(title):
        raise ReviewNeeded('候选文件疑似补充材料，请手动核对。')
    expected_doi, _, _, expected_title = identity(meta)
    pdf_doi = doi_of(info.get('/doi') or info.get('/DOI'))
    if pdf_doi and expected_doi and pdf_doi != expected_doi:
        raise ReviewNeeded('候选 PDF 的 DOI 与库内论文不符。')
    found = set()
    for match in DOI_RE.finditer(opening):
        value = match.group().lower().rstrip('.,;')
        while value.endswith(')') and value.count(')') > value.count('('):
            value = value[:-1]
        found.add(value)
    if expected_doi and found and found != {expected_doi} and pdf_doi != expected_doi:
        raise ReviewNeeded('候选 PDF 首页的 DOI 与库内论文不符。')
    # A mention in the abstract/body can be a citation to the desired work.
    front_matter = re.split(r'^\s*(?:abstract|introduction|references)\s*[:. ]?\s*$', opening,
                            flags=re.I | re.M)[0][:4000]
    wanted = normalized_title(expected_title)
    known_title = len(wanted) >= 10 and expected_title.lower() not in ('untitled', 'untitled paper') and not doi_of(expected_title)
    if known_title and (normalized_title(title) == wanted or wanted in normalized_title(front_matter)):
        return
    if not known_title and expected_doi and (pdf_doi == expected_doi or found == {expected_doi}):
        return
    raise ReviewNeeded('无法从候选 PDF 的首页或文档属性确认论文身份。')


def source(provider, url, version='unknown'):
    return {'provider': provider, 'url': url, 'version': version}


def publisher_pdf(doi):
    prefix, _, suffix = doi.partition('/')
    if prefix in {'10.1186', '10.1007'}:
        return 'https://link.springer.com/content/pdf/' + quote(doi, safe='/') + '.pdf'
    if prefix == '10.1038':
        return 'https://www.nature.com/articles/' + quote(suffix, safe='') + '.pdf'
    if prefix == '10.3389':
        return 'https://www.frontiersin.org/articles/' + quote(doi, safe='/') + '/pdf'
    return ''


async def landing(client, meta, limit):
    doi = identity(meta)[0]
    url = meta.get('article_url') or meta.get('url') or ('https://doi.org/' + doi if doi else '')
    if not permitted(url, LANDING_DOMAINS):
        return []
    final, content = await read_url(client, url, LANDING_DOMAINS, limit)
    if content.startswith(b'%PDF-'):
        return [(source('文章页面', final), content, '')]
    parser = CitationTags()
    parser.feed(content[:2000000].decode('utf-8', errors='replace'))
    if doi and parser.doi and doi_of(parser.doi) != doi:
        raise SourceError('文章页面 DOI 与库内论文不符。')
    address = urljoin(final, parser.pdf)
    return [(source('文章页面', address), None, '')] if parser.pdf and permitted(address, PDF_DOMAINS) else []


async def pmc_candidates(meta):
    try:
        records = await pmc_materials.article_manifests(meta)
    except pmc_materials.PMCError as exc:
        raise SourceError(str(exc)) from exc
    result = []
    for item in records:
        location = pmc_materials.object_location(item.get('pdf_url') or '', f"{item['pmcid']}.{item['version']}")
        if location:
            url, md5 = location
            result.append((source('PubMed Central', url, 'accepted' if item.get('is_manuscript') else 'published'), None, md5))
    return result


async def index_candidates(client, meta, index):
    doi, pmid, _, _ = identity(meta)
    if index == 'OpenAlex':
        if not doi:
            return []
        url = 'https://api.openalex.org/works/https://doi.org/' + quote(doi, safe='/') + '?select=doi,locations,best_oa_location,open_access'
    else:
        if not doi and not re.fullmatch(r'\d{1,9}', pmid):
            return []
        query = f'DOI:"{doi}"' if doi else f'EXT_ID:{pmid} AND SRC:MED'
        url = 'https://www.ebi.ac.uk/europepmc/webservices/rest/search?' + urlencode(
            {'query': query, 'resultType': 'core', 'format': 'json', 'pageSize': 1})
    _, content = await read_url(client, url, INDEX_HOSTS, 4 * 1024 * 1024)
    try:
        reply = json.loads(content)
        if index == 'OpenAlex':
            if doi_of(reply.get('doi')) != doi:
                return []
            locs = [reply.get('best_oa_location'), *(reply.get('locations') or [])]
            versions = {'publishedVersion': 'published', 'acceptedVersion': 'accepted', 'submittedVersion': 'preprint'}
            result = [source(index, loc['pdf_url'], versions.get(loc.get('version'), 'unknown')) for loc in locs
                      if loc and loc.get('is_oa') and loc.get('pdf_url')]
            is_open = reply.get('open_access', {}).get('is_oa')
        else:
            records = reply.get('resultList', {}).get('result') or []
            if not records:
                return []
            record = records[0]
            if (doi and doi_of(record.get('doi')) != doi) or (not doi and str(record.get('id')) != pmid):
                return []
            result = [source(index, loc['url']) for loc in record.get('fullTextUrlList', {}).get('fullTextUrl', [])
                      if loc.get('documentStyle') == 'pdf' and loc.get('availabilityCode') in ('OA', 'F') and loc.get('url')]
            is_open = record.get('isOpenAccess') == 'Y'
        if is_open and doi and publisher_pdf(doi):
            result.append(source('出版商', publisher_pdf(doi), 'published'))
        return [(candidate, None, '') for candidate in result]
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise SourceError(f'{index} 返回的记录无法读取。') from exc


async def obtain(meta, limit):
    doi, pmid, pmcid, _ = identity(meta)
    if not (doi or pmid or pmcid or permitted(meta.get('article_url') or meta.get('url') or '', LANDING_DOMAINS)):
        return {'outcome': 'not_found', 'message': '请先补全 DOI、PMID 或文章网址。'}
    # Do not let a slow publisher prevent the fallback indexes from being tried.
    seen, failures, reviews = set(), [], []
    checked = 0
    providers = []
    if doi or permitted(meta.get('article_url') or meta.get('url') or '', LANDING_DOMAINS):
        providers.append('文章页面')
    if doi or pmid or pmcid:
        providers.append('PubMed Central')
    if doi or pmid:
        providers.append('Europe PMC')
    if doi:
        providers.append('OpenAlex')
    async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=8), follow_redirects=False) as client:
        for provider in providers:
            if len(seen) >= 12:
                break
            try:
                if provider == '文章页面':
                    pending = landing(client, meta, limit)
                elif provider == 'PubMed Central':
                    pending = pmc_candidates(meta)
                else:
                    pending = index_candidates(client, meta, provider)
                candidates = await asyncio.wait_for(pending, timeout=20)
                checked += 1
            except (SourceError, asyncio.TimeoutError) as exc:
                failures.append(str(exc) or '来源请求超时。')
                continue
            for candidate, content, checksum in candidates:
                url = candidate['url']
                if url in seen or not permitted(url, PDF_DOMAINS):
                    continue
                seen.add(url)
                try:
                    if SUPPLEMENT_PATH.search(unquote(urlsplit(url).path)):
                        raise ReviewNeeded('下载链接指向补充材料，请手动核对。')
                    if content is None:
                        final, content = await asyncio.wait_for(read_url(client, url, PDF_DOMAINS, limit), timeout=35)
                        candidate = {**candidate, 'url': final}
                        if final != url and SUPPLEMENT_PATH.search(unquote(urlsplit(final).path)):
                            raise ReviewNeeded('下载链接指向补充材料，请手动核对。')
                    if checksum and hashlib.md5(content, usedforsecurity=False).hexdigest().lower() != checksum.lower():
                        raise SourceError('下载文件与来源公布的校验值不符。')
                    await asyncio.to_thread(verify_pdf, content, meta)
                    return {'outcome': 'downloaded', 'message': '正文 PDF 已下载。', 'source': candidate, 'content': content}
                except ReviewNeeded as exc:
                    reviews.append({'outcome': 'needs_review', 'message': str(exc), 'source': candidate})
                except (SourceError, asyncio.TimeoutError) as exc:
                    failures.append(str(exc) or '下载超时。')
                if len(seen) >= 12:
                    break
    if reviews:
        return reviews[0]
    if failures and (seen or not checked):
        return {'outcome': 'failed', 'message': failures[0]}
    message = '本次没有找到可自动获取的全文。'
    if failures:
        message += ' 部分来源未能查询：' + failures[-1]
    return {'outcome': 'not_found', 'message': message}


def publish(stage, directory):
    """Install only the parsed main document and its indexes, reverting on write failure."""
    files = [p for p in stage.rglob('*') if p.is_file()]
    files.sort(key=lambda p: p.name == 'meta.json')
    backup_root = stage / '.rollback'
    changed = []
    try:
        for item in files:
            relative = item.relative_to(stage)
            target = directory / relative
            backup = None
            if target.exists():
                if relative.parts[0] == 'figures':
                    raise OSError('A local figure already occupies the staged image ID.')
                if relative.parts[0] == 'blocks':
                    # An existing cache can be rebuilt lazily. Leave its recovery path intact.
                    continue
                backup = backup_root / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                # Preserve a complete live copy between the snapshot and atomic replacement.
                try:
                    os.link(target, backup)
                except OSError:
                    shutil.copy2(target, backup)
            changed.append((target, backup))
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(item, target)
    except BaseException:
        for target, backup in reversed(changed):
            if backup is None:
                target.unlink(missing_ok=True)
            else:
                os.replace(backup, target)
        raise
