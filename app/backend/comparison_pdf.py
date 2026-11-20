"""Bounded, reproducible comparison citations from local PDF text blocks."""
import hashlib
import json
import re

from fastapi import HTTPException
from chat_sources import load_document_pages

PER_PAPER_PAGES = 120
PER_DRAFT_PAGES = 600
PER_CELL = 8
KEY = re.compile(r'pdf:(main|[0-9a-f]{32}):p([1-9][0-9]{0,2})-b\d{1,6}:\d{1,5}:[a-f0-9]{64}')
PRIOR_WORK = re.compile(r'\bet al\.|\b(?:previous|earlier|prior|another|published) (?:stud(?:y|ies)|reports?|trials?)\b'
                        r'|\b(?:previously reported|reported elsewhere)\b|既往研究|先前研究|已有研究|他人研究', re.I)


def sentences(paper_id, doc, block):
    if PRIOR_WORK.search(block['text']):
        return
    for n, part in enumerate(re.split(r'(?<=[。！？!?;；])|(?<=\.)(?=\s|$)', block['text'])):
        quote = part.strip()
        if not quote:
            continue
        bbox = block.get('bbox')
        identity = [paper_id, doc['revision'], block['text'], bbox, quote]
        digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        key = f"pdf:{doc['id']}:{block['id']}:{n}:{digest}"
        yield dict(key=key, kind='pdf', label='PDF 原文', document_id=doc['id'], document_name=doc['name'],
                   page=block['page'], text=quote, source_revision=doc['revision'], bbox=bbox,
                   figure_id='', figure_label='', status='', available=True)


def page_plan(documents, budget):
    # In a long paper every attached PDF gets a turn before one consumes the budget.
    plan = {doc['id']: [] for doc in documents}
    for page in range(1, max((d['pages'] for d in documents), default=0) + 1):
        for doc in documents:
            if not budget:
                return plan
            if page <= doc['pages']:
                plan[doc['id']].append(page)
                budget -= 1
    return plan


def scan(directory, meta, material, coverage, *, budget=PER_PAPER_PAGES, keys=None):
    documents = material['documents']
    if keys is None:
        present = {doc['id'] for doc in documents}
        expected = [{'id': 'main', 'filename': meta.get('filename') or '正文', 'pages': meta.get('pages', 0)},
                    *meta.get('supplements', [])]
        for doc in expected:
            if doc['id'] not in present:
                coverage.append({'document_id': doc['id'], 'document_name': doc['filename'], 'total': doc['pages'],
                                 'examined': 0, 'requested': 0, 'no_text': [], 'error': 'PDF 文件不可用'})
    if keys is None:
        plan = page_plan(documents, budget)
    else:
        plan = {doc['id']: set() for doc in documents}
        for key in keys:
            match = KEY.fullmatch(key)
            if match and match[1] in plan:
                plan[match[1]].add(int(match[2]))
    for doc in documents:
        wanted = sorted(page for page in plan[doc['id']] if page <= doc['pages'])
        info = {'document_id': doc['id'], 'document_name': doc['name'], 'total': doc['pages'],
                'requested': len(wanted), 'examined': 0, 'no_text': [], 'error': ''}
        coverage.append(info)
        if not wanted:
            continue
        try:
            blocks = load_document_pages(directory, meta, doc['id'], wanted)
        except HTTPException as exc:
            info['error'] = str(exc.detail)
            continue
        if any(b['source_revision'] != doc['revision'] for b in blocks):
            info['error'] = '文件版本已变化，请重新起草'
            continue
        text_pages = {b['page'] for b in blocks if b['text'].strip()}
        info['examined'] = len(wanted)
        info['no_text'] = [p for p in wanted if p not in text_pages]
        for block in blocks:
            for source in sentences(meta['id'], doc, block):
                if keys is None or source['key'] in keys:
                    yield source


def resolve(directory, meta, material, key):
    return next(scan(directory, meta, material, [], keys={key}), None)
