"""Suggest reviewable comparison cells from saved, page-verified excerpts."""
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from chat_sources import load_document_pages

Column = Literal['enrolled', 'paired', 'single_cell', 'sites']
PaperId = Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]


class DraftIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    paper_ids: list[PaperId] = Field(min_length=2, max_length=100)
    columns: list[Column] = Field(min_length=1, max_length=4)

    @model_validator(mode='after')
    def validate_selection(self):
        if not self.question.strip() or len(set(self.paper_ids)) != len(self.paper_ids) or len(set(self.columns)) != len(self.columns):
            raise ValueError('请填写研究问题，并选择不同的论文和比较列。')
        return self


class ReviewedCell(BaseModel):
    cohort: str = Field(default='', max_length=160)
    column: Column
    value: str = Field(min_length=1, max_length=4000)
    source_key: str = Field(min_length=1, max_length=160)


# A number without an explicit person unit must never become a patient count.
person = r'(?:patients?|participants?|donors?|subjects?)'
people = rf'(?<![\w.,\-])\d+(?:,\d{{3}})*\s+{person}\b'
zh_people = r'(?<![\d.])\d+\s*(?:名|位|例)\s*(?:受试者|患者|病人|供者|参与者)'
enroll = r'(?:enroll?ed|enrolment|enrollment|recruited|included)'
pair = r'(?:\b(?:paired|matched|pairs)\b|配对)'
single = r'(?:single[-\s]cell|scRNA[-\s]?seq|单细胞)'
sampling = r'(?:\b(?:collected|sampled|biopsied|obtained|specimens?|samples?|biopsies|tissues?)\b|取样|取材|采样|采集|标本|样本|活检)'
anatomy = r'(?:\b(?:breast|liver|lung|brain|bone|kidney|blood|marrow|lymph nodes?|primary tumou?rs?|metasta\w+)\b|乳腺|肝|肺|脑|骨|血液|淋巴结|原发灶|转移灶)'
topics = {'enrolled': rf'{enroll}|入组|纳入|招募', 'paired': pair, 'single_cell': single, 'sites': sampling}
cohort_re = re.compile(r'\b(?:discovery|validation|training|replication|test)\s+cohort(?:\s+(?:[A-Z]|\d+)\b)?\b|(?:发现|验证|训练|复现|测试)队列(?:\s*[A-Z0-9]+)?|\bcohort\s+(?:[A-Z]|\d+)\b|队列\s*(?:[A-Z]|\d+)', re.I)
supp_table = re.compile(r'(?:supplement(?:al|ary)?[ _-]+tables?(?:[ _-]*S?\s*\d+[a-z]?)?'
                        r'|\btable[ _-]*S[ _-]*\d+[a-z]?|(?:补表|补充表|补充材料表)(?:\s*[SＳ]?\s*\d+[a-z]?)?)'
                        r'(?:\s*(?:[-–—、,/&]|and|to|through|和|至)\s*[SＳ]?\d+[a-z]?)*', re.I)
referral = re.compile(r'\b(?:see|listed|provided|reported|detailed|shown|available|summari[sz]ed|given|described|presented)\b|\b(?:are|is|appear)\s+(?:only\s+)?in\b|参见|详见|见补|见表|列于|列在', re.I)
uncertain = re.compile(r'\b(?:if|whether|assuming|not|no|without|unavailable|missing|lacked|lacking|failed|unsuccessful|future|unknown|unclear|excluded|estimated|planned|target(?:ed)?|would|may|might|aim(?:ed)?|expected|intend(?:ed)?|approximately|about|roughly|at least|at most|fewer than|more than|less than|over|under)\b|[<>≤≥]|至少|最多|不超过|超过|大约|如果|假设|计划|拟入组|预计|不详|未报告|未知|未说明|未明确|未进行|未配对|未纳入|未获得|未能|无法|失败|缺少|缺失|缺乏|留待|拟用于|排除|[?？]', re.I)


def clean(text):
    return ' '.join(unicodedata.normalize('NFKC', text).split())


def quote_on_page(quote, page):
    needle = clean(quote)
    if not needle:
        return False
    # Do not match "12 patients" inside "112 patients" or "count was 1" inside "12".
    left = r'(?<![A-Za-z0-9_])' if needle[0].isascii() and needle[0].isalnum() else ''
    right = r'(?![A-Za-z0-9_])' if needle[-1].isascii() and needle[-1].isalnum() else ''
    return re.search(left + re.escape(needle) + right, page) is not None


def labelled_units(quote):
    for sentence in re.split(r'[;；。！!]|(?<=[?？])|\.(?=\s|$)', quote):
        if sentence.rstrip().endswith(('?', '？')):
            continue
        # Do not let one cohort's label cross a contrast or a clause boundary.
        for unit in re.split(r'\b(?:whereas|while|but)\b|然而|相比之下', sentence, flags=re.I):
            # An introductory cohort label can govern the whole unit. A label
            # attached to a later clause cannot be propagated backwards.
            prefix = re.match(r'^\s*(?:(?:in|within|among|for)\s+|在|对于)?(?:the\s+)?('
                              + cohort_re.pattern + r')(?:中|内)?\s*[,，:：]\s*', unit, re.I)
            context = prefix[1].casefold() if prefix else ''
            content = unit[prefix.end():] if prefix else unit
            # Separate independent operations/denominators, but keep lists of sampling sites.
            parts = re.split(r'(?:(?<![0-9])[,，]|[,，](?=\s))\s*(?:of whom\s+|其中\s*)?(?=(?:\d|we\b|[A-Z][a-z]+\s+were))|\s+and\s+(?=(?:(?:we\s+)?(?:profiled|analy[sz]ed|collected|enrolled)|\d))', content, flags=re.I)
            for part in parts:
                value = part.strip(' ,，\n')
                names = {match.group().casefold() for match in cohort_re.finditer(value)}
                if value and len(value) <= 4000 and len(names) <= 1:
                    yield next(iter(names), context), value


# Each count needs a grammatical link to its own operation. In particular
# "single-cell analysis included 6 patients from the paired group" describes
# neither study enrollment nor the number of patients with complete pairs.
paired_material = rf'(?:{pair}.{{0,65}}(?:specimens?|samples?|biopsies|tumou?rs?|tissues?|metastases|取样|取材|标本|样本|原发灶|转移灶)|(?:samples?|specimens?|biopsies|标本|样本).{{0,40}}{pair})'
profiled = r'(?:profiled|sequenced|analy[sz]ed|assayed)'
assay = rf'(?:{single})(?:[-\s]+RNA)?[-\s]*(?:analys[ie]s|sequencing|profiling)?'
assay_link_before = re.compile(
    rf'{assay}\s+(?:included|of|involved|covered)\s*$'
    rf'|{assay}\s+(?:was|were)\s+(?:performed|conducted|completed)\s+'
    r'(?:(?:on|using)\s+(?:samples?|tissues?|cells?)\s+)?(?:on|in|for|from)\s*$'
    rf'|\b{profiled}\s+(?:(?:samples?|tissues?|cells?)\s+from\s+)?$'
    r'|单细胞(?:测序|分析).{0,12}(?:实际纳入|包括|纳入|涉及|共)\s*$', re.I)
assay_link_after = re.compile(
    rf'^\s*(?:(?:underwent|entered)\s+(?:the\s+)?{assay}|(?:were|was)\s+{profiled}'
    rf'|(?:实际)?(?:进入|接受|纳入|用于|进行了)(?:了)?单细胞)', re.I)
per_group = re.compile(r'\b(?:per|in each|from each|in every)\s+(?:group|arm|cohort)|\beach\s+(?:group|arm|cohort)|每组|每个队列|各组|各纳入', re.I)

between_people = re.compile(
    r'\b(?:age|sex|gender|propensity|frequency|ethnicity|stage)[-\s]*(?:and[-\s]*)?(?:matched|matching)\b'
    r'|\bmatch(?:ed|ing)\s+(?:by|on|for)\s+(?:age|sex|gender|stage|propensity)\b'
    r'|\b(?:controls?|unrelated|independent|unpaired|between[- ]patient|different (?:patients?|donors?)|technical replicates?|aliquots?|paired[- ]end|DNA and RNA|RNA and DNA)\b'
    r'|(?:性别|年龄|倾向评分|分期).{0,5}(?:配对|匹配)|独立(?:患者|人群|队列)|不同患者|对照人群|组间匹配|技术重复|双端测序|双组学', re.I)
subject_count = re.compile(rf'{people}|{zh_people}', re.I)


def proposed_value(column, clause, source):
    if uncertain.search(clause):
        return None
    if column == 'sites':
        if source.get('field') == 'tissue' and quote_on_page(source['field_value'], clean(clause)):
            return source['field_value']
        return clause if re.search(sampling, clause, re.I) and re.search(anatomy, clause, re.I) else None

    count = subject_count.search(clause)
    if not count or per_group.search(clause) or not re.search(topics[column], clause, re.I):
        return None
    # Two numbers can denote a sample/donor distinction or a subset. This
    # first edition only proposes a count when its denominator is unambiguous.
    if len(re.findall(r'[0-9]+(?:,[0-9]{3})*', cohort_re.sub('', clause))) != 1:
        return None
    before, after = clause[:count.start()], clause[count.end():]
    if column == 'enrolled':
        if re.search(rf'{pair}|{single}|\banalys[ie]s\b|分析', clause, re.I):
            return None
        described = (re.search(rf'{enroll}\s+(?:(?:a\s+total\s+of|overall|exactly)\s+)?$|(?:入组|纳入|招募)\s*(?:了|共计|共)?\s*$', before, re.I)
                     or re.match(rf'\s+(?:(?:were|were\s+initially|had\s+been)\s+)?{enroll}\b|\s*(?:被|成功)?(?:入组|纳入)', after, re.I))
    elif column == 'paired':
        if between_people.search(clause):
            return None
        if not re.search(paired_material, clause, re.I) or re.search(r'\b(?:partially|incomplete|unpaired)\b|不完整|部分配对', clause, re.I):
            return None
        described = (re.search(rf'{paired_material}.{{0,75}}(?:from|for|in|来自|涉及)\s*$', before, re.I)
                     or re.match(rf'\s*(?:(?:had|with|provided|contributed|yielded|underwent)\s+|(?:具有|有|提供|进行了|接受|完成|获得|均有)).{{0,50}}{paired_material}', after, re.I))
    else:
        if re.search(r'\b(?:subset|subgroup|some|several|eligible|anticipated)\b|部分|亚组|子集', clause, re.I):
            return None
        described = assay_link_before.search(before) or assay_link_after.match(after)
    return count.group() if described else None


def verified_sources(directory, meta, material, keys=None):
    docs = {d['id']: d for d in material['documents']}
    by_document = defaultdict(list)
    for source in material['sources']:
        if (source['kind'] in ('evidence', 'field') and source['available'] and source['text'].strip()
                and source['status'] != 'rejected' and (keys is None or source['key'] in keys)):
            by_document[source['document_id']].append(source)
    checked, warnings = [], []
    for doc_id, sources in by_document.items():
        pages = sorted({s['page'] for s in sources})
        try:
            blocks = load_document_pages(directory, meta, doc_id, pages)
        except HTTPException as exc:
            warnings.append(f"{docs[doc_id]['name']}：{exc.detail}")
            continue
        page_text = defaultdict(list)
        for block in blocks:
            if block['source_revision'] == docs[doc_id]['revision']:
                page_text[block['page']].append(block['text'])
        texts = {page: clean(' '.join(parts)) for page, parts in page_text.items()}
        for source in sources:
            if quote_on_page(source['text'], texts.get(source['page'], '')):
                checked.append(source)
            else:
                warnings.append(f"{docs[doc_id]['name']} 第 {source['page']} 页有摘录未能在当前版本定位。")
    return checked, list(dict.fromkeys(warnings))


def refers_to_table(column, clause):
    if not referral.search(clause) or uncertain.search(clause):
        return False
    if column == 'sites':
        return bool(re.search(r'sampl(?:ing|e) sites?|sites? of (?:collection|sampling)|取样部位|取材部位|采样部位', clause, re.I))
    return (re.search(topics[column], clause, re.I)
            and re.search(rf'\b{person}\b|患者|受试者|入组人数|配对人数|单细胞分析人数', clause, re.I))


def attachment_identities(directory, meta, material):
    present = {d['id'] for d in material['documents']}
    records = [doc for doc in meta.get('supplements', []) if doc['id'] in present]
    for other in meta.get('supplement_files', []):
        digest = other.get('sha256', '')
        if re.fullmatch(r'[0-9a-f]{64}', digest) and (directory / 'supplement-files' / digest).is_file():
            records.append(other)
    identities = []
    for record in records:
        named = set()
        if Path(record.get('filename', '')).suffix.lower() == '.zip':
            identities.append(named)
            continue
        for name in (Path(record.get('filename', '')).stem, record.get('source_label', '')):
            # Only standalone, explicitly numbered labels exclude other tables.
            # A vendor basename, a table range or a combined supplement is ambiguous.
            identifier = re.fullmatch(r'(?:supplementary|supplemental|supplement|supp|补充材料|补充|补)?'
                                      r'(fig(?:ure)?s?|tables?|图|表)s?(\d+[a-z]?)', re.sub(r'[\s._-]', '', clean(name).casefold()))
            if identifier:
                kind = 'table' if identifier[1] in ('table', 'tables', '表') else 'figure'
                named.add((kind, identifier[2]))
        identities.append(named)
    return identities


def supplement_status(label, attachments):
    numbers = re.findall(r'\d+', clean(label))
    # Number ranges and multi-table pointers can refer to several worksheets.
    # For those, review available tables before declaring anything missing.
    for named in attachments:
        if not named or len(named) > 1:
            return 'check_supplement'
        kind, identifier = next(iter(named))
        if kind == 'table' and (len(numbers) != 1 or re.match(r'\d+', identifier).group() in numbers):
            return 'check_supplement'
    return 'missing_supplement'


def requested_pair_sites(question):
    primary = re.compile(r'\bprimary\b|原发', re.I)
    metastatic = re.compile(r'\bmetasta(?:sis|ses|tic)\b|转移', re.I)
    if not (primary.search(question) and metastatic.search(question)):
        return []
    organs = [re.compile(pattern, re.I) for pattern in (r'\bliver\b|肝', r'\blung\b|肺', r'\bbrain\b|脑', r'\bbone\b|骨')]
    specified = [organ for organ in organs if organ.search(question)]
    return [primary, metastatic, *(specified if len(specified) == 1 else [])]


def paper_draft(directory, meta, material, columns, keys=None, question=''):
    sources, warnings = verified_sources(directory, meta, material, keys)
    attachments = attachment_identities(directory, meta, material)
    cohorts = {}
    pairing_sites = requested_pair_sites(question)

    def new_cells():
        return {column: {'status': 'not_found', 'candidates': []} for column in columns}

    for source in sources:
        for cohort, clause in labelled_units(source['text']):
            table = '、'.join(match.group().strip() for match in supp_table.finditer(clause))
            for column in columns:
                value = proposed_value(column, clause, source)
                if column == 'paired' and value and not all(site.search(clause) for site in pairing_sites):
                    continue
                if not value and not (table and refers_to_table(column, clause)):
                    continue
                if cohort not in cohorts:
                    cohorts[cohort] = new_cells()
                cell = cohorts[cohort][column]
                if value:
                    item = {'value': value, 'source': source}
                    if item not in cell['candidates']:
                        cell['candidates'].append(item)
                    cell['status'] = 'candidates'
                    cell.pop('reason_source', None)
                    cell.pop('supplement_label', None)
                elif cell['status'] != 'candidates':
                    status = supplement_status(table, attachments)
                    if cell['status'] == 'not_found' or status == 'check_supplement':
                        cell.update(status=status, reason_source=source, supplement_label=table)
    if not cohorts:
        cohorts[''] = new_cells()
    return {'paper_id': meta['id'], 'title': material['title'], 'warnings': [*material['warnings'], *warnings],
            'cohorts': [{'cohort': name, 'cells': cells} for name, cells in cohorts.items()]}
