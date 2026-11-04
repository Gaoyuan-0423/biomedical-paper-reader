"""Conservative, offline resource candidates from saved deep-reading notes.

Candidates are suggestions, never evidence of an accession's existence. Nothing
here fetches URLs, invents repository links, or assigns PDF provenance.
"""
import hashlib
import json
import re
import unicodedata
from urllib.parse import parse_qs, unquote, urlsplit

from fastapi import HTTPException

from paper_resources import (ACCESSION_TOKEN, NEW_SAMPLE_FIELDS, SAMPLE_FIELDS, ResourceIn, normalize_resource, resource_accessions,
                             resource_key, resource_link_keys, resource_repository, resource_urls, safe_url)


ACCESSION = ACCESSION_TOKEN
URL = re.compile(r'https?://[^\s<>"`\[\]{}|\u3000-\u303f\uff00-\uffef]+', re.I)
SKIP = re.compile(
    r'示例|例如|比如|占位|假设|可能的|未来|建议使用|建议下载|未提供|未报告|未披露|'
    r'\b(?:example|placeholder|hypothetical|future|not (?:provided|reported|available))\b', re.I)
SKIP_HEADING = re.compile(r'未来|展望|建议|假设|示例|参考文献|阅读核查与资源记录|\b(?:references|future|recommendations|examples)\b', re.I)
PREFERRED_HEADING = re.compile(r'数据|方法|代码|资源|\b(?:data|methods?|code|resources?|availability)\b', re.I)
NOTICE = '来自精读笔记自动整理，尚未核对原文。笔记片段：\n'
AUTO_NOTICE = '来自精读笔记自动整理。'


def accession_repository(accession):
    return resource_repository('', accession)


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


def _identifier_resources(notes, legacy=False):
    """Conservative fallback when no dedicated resource table is present."""
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
                notes=NOTICE + raw.strip()[:600] if legacy else AUTO_NOTICE).model_dump())
            key = resource_key(record)
            if key not in candidates:
                candidates[key] = record
            elif not candidates[key]['url'] and record['url']:
                # Enrich candidates only. Existing human records are never updated.
                candidates[key]['url'] = record['url']
    return list(candidates.values())


def identity_keys(record):
    """Also match an existing URL-only record to its explicit accession URL."""
    keys = {resource_key(record), *resource_link_keys(record)}
    info = _url_info(record.get('url', ''))
    if info:
        keys.add(resource_key(info))
    accession = record.get('accession', '')
    if not record.get('repository') and ACCESSION.fullmatch(accession):
        keys.add(resource_key({**record, 'repository': accession_repository(accession)}))
    return keys


HEADERS = {
    'title': ('资源', '资源名称', '数据代码资源', '资源条目', 'resource', 'resources', 'resourcename'),
    'purpose': ('用途', '使用目的', '本文用途', 'purpose', 'use', 'usage'),
    'identifiers': ('编号链接', '编号或链接', '编号', '链接', '入口', 'identifierlink', 'identifierslinks',
                    'accessionlink', 'accessionurl', 'accession', 'identifier', 'identifiers', 'link', 'url'),
    'conditions': ('影响使用的条件', '使用条件', '访问条件', '获取条件', '限制条件', '条件',
                   'conditions', 'conditionsforuse', 'conditionsaffectinguse', 'accessconditions', 'usageconditions', 'restrictions'),
    'sample_details': ('样本', '样品', '材料', '样本材料', '样品材料', '使用样品', '样本类型', '样品类型', '使用样本',
                       'sample', 'samples', 'sampledetails', 'samplematerial', 'sampletype', 'specimen'),
    'organism': ('物种', '研究对象', '物种研究对象', 'species', 'organism', 'organisms'),
    'tissue': ('组织', '部位', '组织部位', '材料来源', 'tissue', 'tissues', 'organ'),
    'disease': ('疾病', '疾病状态', 'disease', 'condition'),
    'modality': ('技术', '技术类型', '测序技术', '测序类型', 'modality', 'technology', 'assay'),
    'platform': ('平台', '测序平台', '检测平台', 'platform', 'sequencingplatform'),
    'sample_groups': ('分组', '实验分组', '论文实验分组', 'groups', 'samplegroups', 'experimentalgroups'),
    'sample_count': ('样本量', '样品数量', '使用样本量', '实际样本量', '论文实际样本量',
                     'samplesize', 'samplecount', 'samplesused'),
}


def structured_samples(details):
    """Extract only explicitly labeled fields; never infer counts or GEO scope."""
    value = unicodedata.normalize('NFKC', re.sub(r'<br\s*/?>', '\n', details or '', flags=re.I))
    value = value.replace('**', '').replace('`', '')
    # Unknown labels also delimit values, so a following GEO total is not
    # swallowed into the preceding paper-specific sample count.
    labels = list(re.finditer(r'(?:^|[;\n])\s*([^:;\n]{1,40})\s*:\s*', value))
    output, conflicts = {}, set()
    for index, match in enumerate(labels):
        name = _header(match[1])
        field = next((field for field in SAMPLE_FIELDS if name in HEADERS[field]), None)
        if field is None:
            continue
        item = value[match.end():labels[index + 1].start() if index + 1 < len(labels) else len(value)].strip(' ;\n')
        if not item or len(item) > 1000 or re.match(r'^(?:未报告|未提供|未知|不适用|未说明|不明确|待确认|not reported|not provided|unknown|not applicable|n/?a\b)', item, re.I):
            continue
        if field in conflicts:
            continue
        if field in output and output[field] != item:
            # Conflicting/repeated labels are not a reliable single field.
            output[field] = ''
            conflicts.add(field)
        else:
            output[field] = item
    return {field: item for field, item in output.items() if item}


def _cells(line):
    """Split Markdown cells, respecting escaped pipes and inline code spans."""
    value = line.strip()
    if '|' not in value:
        return []
    if value.startswith('|'):
        value = value[1:]
    if value.endswith('|') and not value.endswith('\\|'):
        value = value[:-1]
    cells, part, code, index = [], [], 0, 0
    while index < len(value):
        char = value[index]
        if char == '\\' and index + 1 < len(value) and value[index + 1] == '|':
            part.append('|')
            index += 2
            continue
        if char == '`':
            end = index + 1
            while end < len(value) and value[end] == '`':
                end += 1
            length = end - index
            code = 0 if code == length else length if not code else code
            part.append(value[index:end])
            index = end
            continue
        if char == '|' and not code:
            cells.append(''.join(part).strip())
            part = []
        else:
            part.append(char)
        index += 1
    cells.append(''.join(part).strip())
    return cells


def _header(cell):
    cell = unicodedata.normalize('NFKC', cell)
    cell = re.sub(r'\([^)]*\)', '', cell)
    return re.sub(r'[\W_]+', '', cell).casefold()


def _resource_tables(notes):
    """Return (found, rows). Conditions never use the speculative-text filter."""
    lines = notes.splitlines()
    found, rows, headings, fenced, index = False, [], [], False, 0
    while index < len(lines):
        line = lines[index]
        if line.lstrip().startswith(('```', '~~~')):
            fenced = not fenced
            index += 1
            continue
        heading = re.match(r'^\s*(#{1,6})\s+(.+)', line)
        if heading and not fenced:
            level = len(heading[1])
            headings = [(n, text) for n, text in headings if n < level] + [(level, heading[2])]
        skip = fenced or SKIP_HEADING.search(' '.join(text for _, text in headings))
        cells = _cells(line) if not skip else []
        mapping, duplicate_header = {}, False
        for column, cell in enumerate(cells):
            for field, aliases in HEADERS.items():
                if _header(cell) in aliases:
                    duplicate_header = duplicate_header or field in mapping
                    mapping[field] = column
        separator = _cells(lines[index + 1]) if index + 1 < len(lines) else []
        if (duplicate_header or not all(field in mapping for field in ('title', 'purpose', 'identifiers', 'conditions')) or
                len(separator) != len(cells) or not all(re.fullmatch(r':?-{3,}:?', cell.replace(' ', '')) for cell in separator)):
            index += 1
            continue
        found = True
        index += 2
        while index < len(lines):
            values = _cells(lines[index])
            if len(values) != len(cells):
                break
            fields = {field: re.sub(r'<br\s*/?>', '\n', values[column], flags=re.I).strip()
                      for field, column in mapping.items()}
            fields['title'] = fields['title'].strip('*` ')
            if fields['title'] and any(fields.get(field) for field in ('purpose', 'identifiers', 'conditions', 'sample_details')):
                rows.append(fields)
            index += 1
    return found, rows


def _table_resource(fields):
    fields = dict(fields)
    for field, value in structured_samples(fields.get('sample_details', '')).items():
        if not fields.get(field):
            fields[field] = value
    accessions = resource_accessions(fields)
    links = resource_urls(fields.get('identifiers', ''))
    repository = {accession_repository(value) for value in accessions}
    known_links = [info for link in links if (info := _url_info(link))]
    repository.update(info['repository'] for info in known_links)
    kind = 'other'
    if re.search(r'细胞系|抗体|试剂|cell lines?|reagents?', fields['title'], re.I):
        kind = 'other'
    elif accessions or re.search(r'数据|队列|TCGA|IMvigor|dataset|cohort', fields['title'], re.I):
        kind = 'dataset'
    elif re.search(r'代码|软件|code|software', fields['title'], re.I) or known_links and all(item['kind'] == 'code' for item in known_links):
        kind = 'code'
    return normalize_resource(ResourceIn(**fields, kind=kind,
        repository=next(iter(repository)) if len(repository) == 1 else '',
        accession=accessions[0] if len(accessions) == 1 else '',
        url=links[0] if len(links) == 1 else '',
        role=_role(fields.get('purpose', ''), kind), notes=AUTO_NOTICE).model_dump())


def resource_fingerprint(record, version=3):
    fields = {field: record.get(field, ResourceIn.model_fields[field].default) for field in ResourceIn.model_fields
              if version != 2 or field not in NEW_SAMPLE_FIELDS}
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def extract_resources(notes):
    """Prefer whole table rows; fall back to explicit identifiers only."""
    found, rows = _resource_tables(notes)
    candidates = [_table_resource(row) for row in rows] if found else _identifier_resources(notes)
    output, seen = [], set()
    for record in candidates:
        fingerprint = resource_fingerprint(record)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        record['auto_extraction'] = {'version': 3, 'fingerprint': fingerprint,
                                     'source': 'resource_table' if found else 'identifier_fallback'}
        output.append(record)
    return output


def untouched_auto(record):
    """Only records still identical to our output may be replaced automatically."""
    if record.get('status') != 'pending':
        return False
    if set(record) - set(ResourceIn.model_fields) - {'id', 'created_at', 'updated_at', 'source_revision', 'auto_extraction'}:
        return False
    marker = record.get('auto_extraction')
    if isinstance(marker, dict) and marker.get('version') in (2, 3):
        version = marker['version']
        if version == 2 and any(record.get(field) for field in NEW_SAMPLE_FIELDS):
            return False
        return marker.get('fingerprint') == resource_fingerprint(record, version=version)
    if (not record.get('notes', '').startswith(NOTICE) or not record.get('created_at') or
            record.get('created_at') != record.get('updated_at') or record.get('source_revision') or
            any(record.get(field) for field in ('purpose', 'identifiers', 'conditions', 'sample_details'))):
        return False
    # Recreate the legacy record from its own bounded snippet; customized title,
    # role, notes, provenance, or supplemental fields will not match.
    for expected in _identifier_resources(record['notes'][len(NOTICE):], legacy=True):
        if not expected['url'] and record.get('url'):
            info = _url_info(record['url'])
            if info and info['accession'] == expected['accession']:
                expected['url'] = record['url']
        if all(record.get(field, '') == value for field, value in expected.items()):
            return True
    return False


def plan_resource_update(records, candidates):
    """Return new candidates, IDs to replace, and count of unchanged candidates."""
    table = any(item.get('auto_extraction', {}).get('source') == 'resource_table' for item in candidates)
    candidate_keys = set().union(*(identity_keys(item) for item in candidates))
    eligible = {record['id'] for record in records if untouched_auto(record) and
                (table or identity_keys(record).intersection(candidate_keys))}
    retained, removed, new, skipped = [record for record in records if record['id'] not in eligible], set(eligible), [], 0
    for candidate in candidates:
        matching = next((record for record in records if record['id'] in removed and
                         resource_fingerprint(record) == resource_fingerprint(candidate)), None)
        if matching:
            removed.remove(matching['id'])
            retained.append(matching)
            skipped += 1
            continue
        if table:
            duplicate = any(resource_key(record) == resource_key(candidate) for record in retained + new)
        else:
            duplicate = any(identity_keys(record).intersection(identity_keys(candidate)) for record in retained + new)
        if duplicate:
            skipped += 1
        else:
            new.append(candidate)
    return new, removed, skipped
