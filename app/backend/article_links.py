"""Inspect scholarly article links, keeping only bibliographic metadata in previews."""
import asyncio
import hashlib
import io
import json
import re
from collections import defaultdict
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from pypdf import PdfReader

import oa_fulltext as oa
from external import DOI_RE, normalize_doi
from public_download import ArticleLinkError, PDF_LIMIT, fetch_public, public_url


class ArticleIdentityError(ArticleLinkError):
    """Different scholarly records or a supplemental document were identified."""


def doi_path_matches(actual_doi, path_doi):
    actual, path = oa.doi_of(actual_doi), oa.doi_of(path_doi)
    return bool(actual and path and path in {actual + tail for tail in
                ('', '.pdf', '/full', '/abstract', '/pdf', '/epdf', '/fulltext')})


PDF_LABEL = re.compile(r'(?:(?:download|view|open|read)\s+(?:the\s+)?(?:article\s+|full[ -]?text\s+)?'
                       r'(?:as\s+(?:a\s+)?)?pdf|(?:article\s+|full[ -]?text\s+)?pdf(?:\s+(?:download|version))?'
                       r'|(?:下载|阅读|查看)(?:全文)?\s*pdf|pdf\s*全文)', re.I)


class ArticleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fields = defaultdict(list)
        self.json_blocks = []
        self.capture_json = False
        self.json_text = []
        self.skipped_sections = []
        self.heading = None
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag in ('nav', 'aside', 'footer'):
            self.skipped_sections.append(tag)
        if not self.skipped_sections and tag == 'h1':
            self.heading = []
        if not self.skipped_sections and tag == 'a':
            self.anchor = (fields, [])
        if tag == 'meta':
            name = (fields.get('name') or fields.get('property') or '').lower()
            content = fields.get('content') or ''
            if name.startswith(('citation_', 'dc.', 'dc:', 'dcterms.', 'prism.', 'og:')) and content:
                self.fields[name.replace('dc:', 'dc.')].append(content[:16000])
        elif tag == 'link' and (fields.get('type') or '').lower() == 'application/pdf':
            if fields.get('href'):
                self.fields['pdf_link'].append(fields['href'])
        elif tag == 'script' and (fields.get('type') or '').lower().split(';')[0] == 'application/ld+json':
            self.capture_json = True
            self.json_text = []

    def handle_data(self, data):
        if self.capture_json:
            self.json_text.append(data)
        elif not self.skipped_sections:
            if self.heading is not None:
                self.heading.append(data)
            if self.anchor:
                self.anchor[1].append(data)

    def handle_endtag(self, tag):
        if self.skipped_sections and tag == self.skipped_sections[-1]:
            self.skipped_sections.pop()
        if tag == 'h1' and self.heading is not None:
            self.fields['page_heading'].append(''.join(self.heading))
            self.heading = None
        if tag == 'a' and self.anchor:
            attrs, text = self.anchor
            self.anchor = None
            labels = [_text(attrs.get('aria-label')), _text(attrs.get('title')), _text(''.join(text))]
            description = ' '.join(labels).lower()
            href = attrs.get('href') or ''
            label_is_pdf = any(PDF_LABEL.fullmatch(v) for v in labels)
            if re.search(r'\.pdf(?:[?#]|$)', href, re.I):
                label_is_pdf = label_is_pdf or any(v.lower() in ('download', 'full text', 'download full text', '下载', '下载全文') for v in labels)
            if href and label_is_pdf and not re.search(r'supplement|appendix|supporting|附录|补充', description):
                self.fields['anchor_pdf'].append(href)
        if tag == 'script' and self.capture_json:
            self.capture_json = False
            try:
                self.json_blocks.append(json.loads(''.join(self.json_text)))
            except (ValueError, RecursionError):
                pass
            self.json_text = []


def _text(value, limit=1200):
    return ' '.join(value.split())[:limit] if isinstance(value, str) else ''


def _title(value):
    title = _text(value, 1000)
    if (len(oa.normalized_title(title)) < 10 or not any(c.isalpha() for c in title)
            or title.lower() in {'untitled document', 'untitled paper', 'microsoft word', 'full text pdf'}
            or title.lower().endswith(('.doc', '.docx', '.pdf', '.dvi', '.tex', '.ps', '.indd')) or '://' in title or oa.doi_of(title)):
        return ''
    return title


def _unique(values, name):
    unique = {v for v in values if v}
    if len(unique) > 1:
        raise ArticleIdentityError(f'来源列出了不同的 {name}，请手动核对。')
    return next(iter(unique), '')


def _ids(value):
    """Only take identifiers explicitly attached to this work, not its references."""
    if isinstance(value, dict):
        key = str(value.get('propertyID') or '').lower()
        val = value.get('value') or value.get('@id') or ''
        yield (f'{key}:{val}' if key in ('pmid', 'pmcid') else val)
    elif isinstance(value, str):
        yield value


def _scholarly_nodes(blocks):
    for block in blocks:
        for entry in block if isinstance(block, list) else [block]:
            if not isinstance(entry, dict):
                continue
            graph = entry.get('@graph')
            nodes = [entry, entry.get('mainEntity')] + (graph if isinstance(graph, list) else [])
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                types = node.get('@type', [])
                if not isinstance(types, list):
                    types = [types]
                if any(str(t).split('/')[-1] in ('ScholarlyArticle', 'MedicalScholarlyArticle') for t in types):
                    yield node


def _metadata(html, final_url):
    page = ArticleHTML()
    try:
        page.feed(html)
        page.close()
    except (ValueError, AssertionError, RecursionError) as exc:
        raise ArticleLinkError('无法解析论文网页。') from exc
    fields = page.fields
    for node in _scholarly_nodes(page.json_blocks):
        name = _title(node.get('headline') or node.get('name'))
        if name:
            fields['json_title'].append(name)
        for key, field in [('author', 'json_author'), ('isPartOf', 'json_journal'),
                           ('encoding', 'json_pdf'), ('identifier', 'json_id')]:
            entries = node.get(key) or []
            for entry in entries if isinstance(entries, list) else [entries]:
                if field == 'json_id':
                    fields[field].extend(_ids(entry))
                elif field == 'json_pdf' and isinstance(entry, dict):
                    if (entry.get('encodingFormat') or entry.get('fileFormat')) == 'application/pdf':
                        fields[field].append(entry.get('contentUrl') or '')
                elif field != 'json_pdf':
                    fields[field].append(entry.get('name') if isinstance(entry, dict) else entry)
        fields['json_date'].append(node.get('datePublished'))
        fields['json_abstract'].append(node.get('abstract') or node.get('description'))
        fields['json_id'].extend(_ids(node.get('sameAs')))
    def items(*keys):
        return [_text(v, 16000) for k in keys for v in fields[k] if _text(v)]

    def first(*keys):
        return next(iter(items(*keys)), '')

    titles = items('citation_title', 'dc.title', 'dcterms.title', 'json_title')
    if len(set(oa.normalized_title(t) for t in items('json_title'))) > 1:
        raise ArticleIdentityError('此网页包含多篇论文，请提供单篇文章链接。')
    metadata_ids = items('citation_doi', 'prism.doi', 'dc.identifier', 'dcterms.identifier', 'json_id')
    dois = [oa.doi_of(s) for s in metadata_ids]
    path = unquote(urlsplit(final_url).path)
    path_doi = re.search(r'(?:^|/)(10\.\d{4,9}/.+?)/?$', path, re.I)
    if path_doi and oa.doi_of(path_doi[1]):
        from_path = oa.doi_of(path_doi[1])
        known = [x for x in dois if x]
        # Some publishers append a view name or file extension after their DOI.
        if not any(doi_path_matches(k, from_path) for k in known):
            dois.append(from_path)
    doi = _unique(dois, 'DOI')
    pmids = items('citation_pmid', 'citation_pubmed_id')
    pmcids = items('citation_pmcid')
    for item in metadata_ids:
        pm = re.fullmatch(r'(?:pmid:\s*|https?://pubmed.ncbi.nlm.nih.gov/)([1-9][0-9]{0,8})/?', item, re.I)
        if pm:
            pmids.append(pm[1])
        pmc = re.fullmatch(r'(?:pmcid:\s*)?(PMC[0-9]+)', item, re.I)
        if pmc:
            pmcids.append(pmc[1].upper())
    pmid = _unique([s for s in pmids if re.fullmatch(r'[1-9][0-9]{0,8}', s)], 'PMID')
    pmcid = _unique([s.upper() for s in pmcids if re.fullmatch(r'PMC[0-9]+', s, re.I)], 'PMCID')
    parsed = urlsplit(final_url)
    arxiv = parsed.hostname in {'arxiv.org', 'www.arxiv.org', 'export.arxiv.org'} and re.fullmatch(
        r'/abs/((?:\d{4}\.\d{4,5}|[A-Za-z.-]+/\d{7})(?:v\d+)?)/?', parsed.path)
    title = _title(next(iter(titles), '') or first('og:title'))
    authors = items('citation_author') or items('dc.creator') or items('json_author')
    journal = first('citation_journal_title', 'prism.publicationname', 'json_journal', 'citation_conference_title')
    scholarly = doi or pmid or pmcid or arxiv or fields['json_title'] or (
        fields['citation_title'] and (authors or journal)) or (fields['dc.title'] and authors and
        re.search(r'article|preprint|research|论文', first('dc.type', 'dcterms.type'), re.I))
    headings = set(items('page_heading'))
    if not title and len(headings) == 1:
        title = _title(next(iter(headings)))
    if oa.SUPPLEMENT_HEADING.search(title) or oa.SUPPLEMENT_PATH.search(path):
        raise ArticleIdentityError('链接指向补充材料，请在对应论文中添加附件。')
    date = first('citation_publication_date', 'citation_date', 'citation_online_date',
                 'prism.publicationdate', 'dc.date', 'dcterms.issued', 'json_date')
    year = re.search(r'\b(?:18|19|20)\d{2}\b', date)
    record = {'title': title, 'doi': doi, 'pmid': pmid, 'authors': '; '.join(dict.fromkeys(authors))[:6000],
              'journal': journal[:1200], 'year': year[0] if year else '',
              'abstract': first('citation_abstract', 'dcterms.abstract', 'dc.description', 'json_abstract')}
    if pmcid:
        record['pmcid'] = pmcid
    candidates = items('citation_pdf_url', 'pdf_link', 'json_pdf')
    if arxiv:
        candidates.append('https://arxiv.org/pdf/' + arxiv[1])
    def usable(candidate):
        try:
            target = public_url(urljoin(final_url, candidate))
        except ArticleLinkError:
            return ''
        return '' if oa.SUPPLEMENT_PATH.search(unquote(urlsplit(target).path)) else target

    pdf = next((target for c in candidates if (target := usable(c))), '')
    if not pdf:
        anchors = {target for c in items('anchor_pdf') if (target := usable(c))}
        if len(anchors) == 1:
            pdf = anchors.pop()
    # A page with an explicit bibliographic identifier may omit its title.
    # The preview importer must resolve that identifier before admitting a new record.
    if not title and not (doi or pmid):
        raise ArticleLinkError('网页中未找到能确认单篇论文的题名和出版信息。')
    if not scholarly:
        if len(headings) != 1 or not pdf:
            raise ArticleLinkError('网页中未找到能确认单篇论文的题名和出版信息。')
        # Pages lacking scholarly metadata must match the displayed H1 to the identified PDF.
        record['title'] = _title(next(iter(headings)))
        record['_verify_heading'] = True
    return record, pdf


def _title_line(line):
    title = _title(line.strip())
    if not title or len(title) > 250 or '@' in title or DOI_RE.search(title):
        return ''
    if re.search(r'^(?:copyright|received|published|submitted|open access|research article|'
                 r'journal of|proceedings of|volume\s+\d|arxiv:|original (?:article|research)|'
                 r'abstract|keywords)\b|\b(?:19|20)\d{2}\b.*\d+\s*[-–]\s*\d+|©', title, re.I):
        return ''
    return title


def _front_title(page, heading):
    # Blank lines in the rendered layout usually separate the running header, title
    # block and authors. Keep consecutive title lines together when a title wraps.
    try:
        layout = page.extract_text(extraction_mode='layout')[:4500]
    except Exception:
        layout = ''
    for block in re.split(r'\n\s*\n', layout)[:8]:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if 1 <= len(lines) <= 4 and all(_title_line(line) for line in lines):
            joined = ' '.join(lines)
            if len(joined) <= 300 and oa.normalized_title(joined) in oa.normalized_title(heading):
                return joined
    return next((candidate for line in heading.splitlines()[:14] if (candidate := _title_line(line))), '')


def _pdf_metadata(content):
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 600:
            raise ArticleLinkError('PDF 已加密或超出 1–600 页范围。')
        info = reader.metadata or {}
        first_page = (reader.pages[0].extract_text() or '')[:12000]
    except ArticleLinkError:
        raise
    except Exception as exc:
        raise ArticleLinkError('链接中的 PDF 无法读取。') from exc
    front = re.split(r'^\s*(?:References|Bibliography|参考文献)\s*$', first_page, maxsplit=1, flags=re.I | re.M)[0]
    heading = re.split(r'^\s*(?:Abstract|Introduction|摘要)\b', front, maxsplit=1, flags=re.I | re.M)[0][:4000]
    if oa.SUPPLEMENT_HEADING.search(front[:1400]) or oa.SUPPLEMENT_HEADING.search(str(info.get('/Title') or '')):
        raise ArticleIdentityError('链接指向补充材料，请在对应论文中添加附件。')
    explicit = oa.doi_of(info.get('/doi') or info.get('/DOI'))
    doi = _unique([explicit] + [normalize_doi(m[0]).lower() for m in DOI_RE.finditer(heading)], 'DOI')
    title = _title(info.get('/Title'))
    if not title:
        title = _front_title(reader.pages[0], heading)
    # An arbitrary PDF with a document title is not enough to identify a paper.
    scholarly = doi or re.search(r'\b(?:abstract|introduction|keywords|methods|figure\s+1)\b|摘要|关键词', front, re.I)
    if not scholarly and title and len(reader.pages) > 1:
        # In some journals the author list occupies the entire first page.
        try:
            opening = (reader.pages[1].extract_text() or '')[:4000]
        except Exception:
            opening = ''
        scholarly = re.search(r'^\s*(?:abstract|keywords|摘要|关键词)\b', opening, re.M | re.I)
    if not title or not scholarly:
        raise ArticleLinkError('无法从 PDF 首页确认论文的题名与文献身份。')
    record = {'title': title, 'doi': doi, 'pmid': '', 'journal': '', 'year': '', 'abstract': '',
              'authors': _text(info.get('/Author'), 6000)}
    try:
        oa.verify_pdf(content, record)
    except oa.ReviewNeeded as exc:
        raise ArticleIdentityError(str(exc)) from exc
    except oa.SourceError as exc:
        raise ArticleLinkError(str(exc)) from exc
    return record


async def _read_pdf(article_url, url, limit):
    article = urlsplit(public_url(article_url)) if article_url else None
    target = urlsplit(public_url(url))
    # Older pages often publish an HTTP PDF link on the same HTTPS journal.
    # Try its secure form first and fall back only to the originally declared URL.
    secure = (article and article.scheme == 'https' and target.scheme == 'http'
              and target.hostname.removeprefix('www.') == article.hostname.removeprefix('www.'))
    if secure:
        try:
            return await fetch_public(urlunsplit(target._replace(scheme='https')), limit)
        except ArticleLinkError:
            return await fetch_public(url, limit)
    else:
        return await fetch_public(url, limit)


async def inspect_url(url):
    """Return a preview record and its file locator. Never retain the fetched file."""
    original = public_url(url)
    final, mime, data = await fetch_public(original)
    link = {'article_url': original, 'inspected': True}
    if data.startswith(b'%PDF-'):
        if any(oa.SUPPLEMENT_PATH.search(unquote(urlsplit(s).path)) for s in (original, final)):
            raise ArticleIdentityError('链接指向补充材料，请在对应论文中添加附件。')
        record = await asyncio.to_thread(_pdf_metadata, data)
        link.update(pdf_url=original, sha256=hashlib.sha256(data).hexdigest())
    else:
        if 'html' not in mime.lower() and not data.lstrip(b'\xef\xbb\xbf \t\r\n').startswith(b'<'):
            raise ArticleLinkError('链接没有返回论文网页或 PDF。')
        charset = re.search(r'charset\s*=\s*["\']?([\w-]+)', mime, re.I)
        try:
            html = data.decode(charset[1] if charset else 'utf-8', errors='replace')
        except (ValueError, LookupError):
            html = data.decode('utf-8', errors='replace')
        record, pdf = _metadata(html, final)
        if record.pop('_verify_heading', False):
            resolved, _, body = await _read_pdf(final, pdf, PDF_LIMIT)
            if oa.SUPPLEMENT_PATH.search(unquote(urlsplit(resolved).path)):
                raise ArticleIdentityError('链接指向补充材料，请在对应论文中添加附件。')
            pdf_record = await asyncio.to_thread(_pdf_metadata, body)
            if oa.normalized_title(pdf_record['title']) != oa.normalized_title(record['title']):
                raise ArticleIdentityError('网页题名与链接中的 PDF 不符，请手动核对。')
            record = pdf_record
            link['sha256'] = hashlib.sha256(body).hexdigest()
        if pdf:
            link['pdf_url'] = pdf
    return {**record, 'url': original, '_link': link}


async def download_pdf(link, expected, limit):
    """Re-fetch the selected public PDF and match it to the confirmed paper."""
    url = link.get('pdf_url') or ''
    source = oa.source('文章页面', url or link.get('article_url') or '')
    if not url:
        return {'outcome': 'not_found', 'message': '该网页未提供可下载的正文 PDF。', 'source': source}
    try:
        final, _, content = await _read_pdf(link.get('article_url'), url, limit)
        source = oa.source('文章页面', final)
        if any(oa.SUPPLEMENT_PATH.search(unquote(urlsplit(s).path)) for s in (url, final)):
            raise oa.ReviewNeeded('下载链接指向补充材料，请手动核对。')
        if link.get('sha256') and hashlib.sha256(content).hexdigest() != link['sha256']:
            raise oa.ReviewNeeded('PDF 与预览时的文件不同，请重新核对。')
        await asyncio.to_thread(oa.verify_pdf, content, expected)
        return {'outcome': 'downloaded', 'message': '正文 PDF 已下载。', 'source': source, 'content': content}
    except oa.ReviewNeeded as exc:
        return {'outcome': 'needs_review', 'message': str(exc), 'source': source}
    except (ArticleLinkError, oa.SourceError) as exc:
        return {'outcome': 'failed', 'message': str(exc), 'source': source}
