"""Conservative, offline resource candidates from saved deep-reading notes.

Candidates are suggestions, never evidence of an accession's existence. Nothing
here fetches URLs, invents repository links, or assigns PDF provenance.
"""
import re
import unicodedata
from urllib.parse import parse_qs, unquote, urlsplit

from fastapi import HTTPException

from paper_resources import ResourceIn, normalize_resource, resource_key, safe_url


ACCESSION = re.compile(
    r'(?<![A-Za-z0-9_])(?:GS[EM]\d+|SR[APRSX]\d+|ER[APRSX]\d+|'
    r'PRJ(?:NA|EB|DB|CA)\d+|[CH]RA\d+|E-(?:MTAB|MEXP|GEOD|TABM|MEXP)-\d+|'
    r'phs\d+(?:\.v\d+\.p\d+)?)(?![A-Za-z0-9_]|-[A-Za-z0-9]|\.[A-Za-z0-9])', re.I)
URL = re.compile(r'https?://[^\s<>"`\[\]{}|\u3000-\u303f\uff00-\uffef]+', re.I)
SKIP = re.compile(
    r'示例|例如|比如|占位|假设|可能的|未来|建议使用|建议下载|未提供|未报告|未披露|'
    r'\b(?:example|placeholder|hypothetical|future|not (?:provided|reported|available))\b', re.I)
SKIP_HEADING = re.compile(r'未来|展望|建议|假设|示例|参考文献|阅读核查与资源记录|\b(?:references|future|recommendations|examples)\b', re.I)
PREFERRED_HEADING = re.compile(r'数据|方法|代码|资源|\b(?:data|methods?|code|resources?|availability)\b', re.I)
NOTICE = '来自精读笔记自动整理，尚未核对原文。笔记片段：\n'


def accession_repository(accession):
    value = accession.upper()
    if value.startswith(('GSE', 'GSM')):
        return 'GEO'
    if value.startswith('SR'):
        return 'SRA'
    if value.startswith('ER'):
        return 'ENA'
    if value.startswith('PRJ'):
        return 'BioProject'
    if value.startswith(('CRA', 'HRA')):
        return 'GSA'
    if value.startswith('E-'):
        return 'ArrayExpress'
    return 'dbGaP'


def _url_info(value):
    """Recognize explicit repository destinations, without rewriting URLs."""
    if not value:
        return None
    try:
        value = safe_url(value)
        parsed = urlsplit(value)
    except (HTTPException, ValueError):
        return None
    host = parsed.hostname
    path = unquote(parsed.path)
    parts = [part for part in path.split('/') if part]
    if host in ('github.com', 'www.github.com', 'gitlab.com', 'www.gitlab.com'):
        if len(parts) < 2 or parts[0].casefold() in {
            'topics', 'collections', 'search', 'explore', 'users', 'groups',
            'help', 'about', 'login', 'signup', 'settings', '-', 'features'}:
            return None
        repository = 'GitHub' if 'github' in host else 'GitLab'
        return dict(repository=repository, accession='', kind='code', url=value,
                    title=repository + ' · ' + '/'.join(parts[:2]))
    repository, kind = '', 'other'
    if host in ('zenodo.org', 'www.zenodo.org') and re.match(r'^/records?/\d+(?:/|$)', path):
        repository = 'Zenodo'
    elif (host == 'figshare.com' or host.endswith('.figshare.com')) and re.match(r'^/(?:articles|collections)/.+/\d+(?:/|$)', path):
        if path.startswith('/articles/journal_contribution/'):
            return None
        repository = 'Figshare'
        if path.startswith('/articles/dataset/'):
            kind = 'dataset'
        elif path.startswith('/articles/software/'):
            kind = 'code'
    elif host in ('datadryad.org', 'www.datadryad.org') and re.match(r'^/(?:stash/)?dataset/doi:10\.5061/dryad\.', path, re.I):
        repository, kind = 'Dryad', 'dataset'
    elif host in ('doi.org', 'dx.doi.org'):
        if re.fullmatch(r'/10\.5281/zenodo\.\d+', path, re.I):
            repository = 'Zenodo'
        elif re.fullmatch(r'/10\.6084/m9\.figshare\.\d+(?:\.v\d+)?', path, re.I):
            repository = 'Figshare'
        elif re.fullmatch(r'/10\.5061/dryad\.[A-Za-z0-9.]+', path, re.I):
            repository, kind = 'Dryad', 'dataset'
    if repository:
        return dict(repository=repository, accession='', kind=kind, url=value,
                    title=repository + ' · ' + path.rstrip('/').split('/')[-1])
    # Only known repository routes can bind an identifier to a URL. An unrelated
    # link near an accession is kept separate, never inferred to describe it.
    allowed = ((host in ('ncbi.nlm.nih.gov', 'www.ncbi.nlm.nih.gov') and
                re.match(r'^/(?:geo/|sra(?:/|$)|bioproject(?:/|$)|projects/gap/)', path)) or
               (host in ('ebi.ac.uk', 'www.ebi.ac.uk') and
                re.match(r'^/(?:ena/browser/view/|arrayexpress/experiments/|biostudies/arrayexpress/studies/)', path)) or
               (host == 'ngdc.cncb.ac.cn' and re.match(r'^/(?:gsa/|gsa-human/|bioproject/)', path)))
    if not allowed:
        return None
    identifiers = {match[0].upper() for match in ACCESSION.finditer(path)}
    for name, values in parse_qs(parsed.query).items():
        if name.casefold() in ('acc', 'term', 'id', 'study_id'):
            for item in values:
                if ACCESSION.fullmatch(item):
                    identifiers.add(item.upper())
    if len(identifiers) != 1:
        return None
    accession = identifiers.pop()
    repository = accession_repository(accession)
    return dict(repository=repository, accession=accession, kind='dataset', url=value,
                title=repository + ' · ' + accession)


def _role(line, kind):
    generated = bool(re.search(r'本研究(?:新)?(?:产生|生成|测定|测序|收集)|本文(?:产生|生成)|(?:generated|produced)\s+(?:in|by|for)\s+(?:this|the present)\s+study', line, re.I))
    reused = bool(re.search(r'复用|再分析|重分析|\b(?:reused?|re-?analys[ei][sd]|reanalysis)\b', line, re.I))
    implementation = kind == 'code' and bool(re.search(r'代码实现|分析代码|源代码|本研究.{0,12}代码|\b(?:implementation|analysis code|source code)\b', line, re.I))
    roles = [name for name, flag in [('generated', generated), ('reused', reused), ('implementation', implementation)] if flag]
    return roles[0] if len(roles) == 1 else 'unknown'


def _lines(notes):
    """Prioritize resource/method sections; exclude proposals and fenced examples."""
    entries, headings, fenced = [], [], False
    for index, raw in enumerate(notes.splitlines()):
        line = unicodedata.normalize('NFKC', raw)
        if line.lstrip().startswith(('```', '~~~')):
            fenced = not fenced
            continue
        if fenced:
            continue
        heading = re.match(r'^\s*(#{1,6})\s+(.+)', line)
        if heading:
            level, label = len(heading[1]), heading[2]
            headings = [(n, text) for n, text in headings if n < level] + [(level, label)]
        labels = ' '.join(text for _, text in headings)
        if SKIP_HEADING.search(labels) or SKIP.search(line):
            continue
        entries.append((not bool(PREFERRED_HEADING.search(labels)), index, raw, line))
    return sorted(entries)


def extract_resources(notes):
    """Return normalized pending suggestions, with duplicates coalesced."""
    candidates = {}
    for _, _, raw, line in _lines(notes):
        items, spans = [], []
        for match in URL.finditer(raw):
            value = match[0].rstrip('.,;:!?')
            while value.endswith(')') and value.count(')') > value.count('('):
                value = value[:-1]
            spans.append(match.span())
            info = _url_info(value)
            if info:
                items.append(info)
        # Strip every URL, even unrecognized ones, so a random paper URL that
        # happens to contain GSE123 cannot create a dataset record.
        plain = list(raw)
        for start, end in spans:
            plain[start:end] = ' ' * (end - start)
        for match in ACCESSION.finditer(unicodedata.normalize('NFKC', ''.join(plain))):
            accession = match[0].upper()
            repository = accession_repository(accession)
            items.append(dict(title=repository + ' · ' + accession, kind='dataset',
                              repository=repository, accession=accession, url=''))
        for info in items:
            record = normalize_resource(ResourceIn(**info, role=_role(line, info['kind']),
                notes=NOTICE + raw.strip()[:600]).model_dump())
            key = resource_key(record)
            if key not in candidates:
                candidates[key] = record
            elif not candidates[key]['url'] and record['url']:
                # Enrich candidates only. Existing human records are never updated.
                candidates[key]['url'] = record['url']
    return list(candidates.values())


def identity_keys(record):
    """Also match an existing URL-only record to its explicit accession URL."""
    keys = {resource_key(record)}
    info = _url_info(record.get('url', ''))
    if info:
        keys.add(resource_key(info))
    accession = record.get('accession', '')
    if not record.get('repository') and ACCESSION.fullmatch(accession):
        keys.add(resource_key({**record, 'repository': accession_repository(accession)}))
    return keys
