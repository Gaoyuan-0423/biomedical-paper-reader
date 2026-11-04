"""Paper-local, revision-bound PDF blocks used as verifiable chat sources."""
import hashlib
import json
import math
import re
import statistics
import threading
import unicodedata
from pathlib import Path

import pdfplumber
from fastapi import HTTPException
from pypdf import PdfReader

from figures import lines_from_words, paragraph_lines, reading_order
from reading_workflow import atomic_json, source_info

_locks = {}
_locks_guard = threading.Lock()
BLOCK_CHARS = 2400
SOURCE_CHARS = 12000


def _cache_lock(directory, document_id):
    with _locks_guard:
        return _locks.setdefault((str(directory), document_id), threading.RLock())


def normalized_text(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value)).casefold()


def document_source(directory, meta, document_id):
    if document_id != 'main' and not re.fullmatch(r'[0-9a-f]{32}', document_id):
        raise HTTPException(404, '来源文件不存在')
    path, pages, name = source_info(directory, meta, document_id)
    if path is None or not path.is_file() or not path.resolve().is_relative_to(Path(directory).resolve()):
        raise HTTPException(404, '来源文件已移除或不存在')
    return path, pages, name


def file_revision(path):
    digest = hashlib.sha256()
    try:
        with path.open('rb') as stream:
            for part in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(part)
    except FileNotFoundError as exc:
        raise HTTPException(404, '来源文件已移除或不存在') from exc
    except OSError as exc:
        raise HTTPException(503, '来源 PDF 暂时无法读取，请检查本地文件后重试') from exc
    return digest.hexdigest()


def valid_bbox(box):
    return (isinstance(box, list) and len(box) == 4 and
            all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in box) and
            box[0] < box[2] and box[1] < box[3])


def _cache_path(directory, document_id):
    return Path(directory) / 'blocks' / (document_id + '.json')


def read_cache(directory, document_id):
    path = _cache_path(directory, document_id)
    if not path.exists():
        return None
    try:
        if path.stat().st_size > 30 * 1024 * 1024:
            raise ValueError()
        cache = json.loads(path.read_text('utf-8'))
        if (not isinstance(cache, dict) or type(cache.get('version')) is not int or cache['version'] not in (1, 2) or
                not re.fullmatch(r'[0-9a-f]{64}', cache.get('source_revision', '')) or
                type(cache.get('page_count')) is not int or not 1 <= cache['page_count'] <= 600 or
                not isinstance(cache.get('pages'), dict)):
            raise ValueError()
        for page, blocks in cache['pages'].items():
            if not page.isdigit() or not 1 <= int(page) <= cache['page_count'] or not isinstance(blocks, list):
                raise ValueError()
            seen = set()
            for block in blocks:
                if (not isinstance(block, dict) or not re.fullmatch(r'p' + page + r'-b\d+', block.get('id', '')) or
                        block['id'] in seen or not isinstance(block.get('text'), str) or
                        not block['text'].strip() or len(block['text']) > BLOCK_CHARS or
                        type(block.get('page')) is not int or block['page'] != int(page) or 'bbox' not in block or
                        block['bbox'] is not None and not valid_bbox(block['bbox'])):
                    raise ValueError()
                seen.add(block['id'])
        return cache
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(409, f'本篇文献的 blocks/{document_id}.json 无法读取，原文件已保留，请检查缓存后重试') from exc


def page_blocks(page, blocks, text=''):
    output = []
    if not blocks and text.strip():
        blocks = [{'text': text, 'bbox': None}]
    for block in blocks:
        value = block['text'].strip()
        for start in range(0, len(value), BLOCK_CHARS):
            quote = value[start:start + BLOCK_CHARS].strip()
            if quote:
                output.append({'id': f'p{page}-b{len(output) + 1}', 'page': page, 'text': quote,
                               'bbox': block.get('bbox') if valid_bbox(block.get('bbox')) else None})
    return output


def save_parsed_blocks(directory, document_id, revision, pages):
    """Reuse import geometry; never destroy a damaged old cache during upload."""
    with _cache_lock(directory, document_id):
        try:
            read_cache(directory, document_id)
        except HTTPException:
            return
        cache = {'version': 2, 'source_revision': revision, 'page_count': len(pages),
                 'pages': {str(page['number']): page_blocks(page['number'], page.get('blocks', []), page.get('text', ''))
                           for page in pages}}
        path = _cache_path(directory, document_id)
        try:
            path.parent.mkdir(exist_ok=True)
            atomic_json(path, cache)
        except OSError:
            # Derived cache is optional. A failed cache write must not turn an
            # already-committed PDF upload into a failed metadata transaction.
            return


def load_document_pages(directory, meta, document_id, wanted):
    path, count, name = document_source(directory, meta, document_id)
    with _cache_lock(directory, document_id):
        revision = file_revision(path)
        cache = read_cache(directory, document_id)
        if cache is None or cache['version'] != 2 or cache['source_revision'] != revision:
            cache = {'version': 2, 'source_revision': revision, 'page_count': count, 'pages': {}}
        missing = [page for page in wanted if str(page) not in cache['pages'] and 1 <= page <= count]
        if missing:
            try:
                with pdfplumber.open(str(path)) as pdf:
                    cache['page_count'] = len(pdf.pages)
                    text_reader = None
                    for number in missing:
                        if number > len(pdf.pages):
                            continue
                        page = pdf.pages[number - 1]
                        if page.rotation:
                            # Geometric column ordering cannot reconstruct rotated
                            # prose reliably; pypdf retains the content-stream order.
                            text_reader = text_reader or PdfReader(path)
                            cache['pages'][str(number)] = page_blocks(number, [], text_reader.pages[number - 1].extract_text() or '')
                            page.close()
                            continue
                        words = page.extract_words(extra_attrs=['size', 'fontname'], use_text_flow=False)
                        lines = reading_order(lines_from_words(words), page.width)
                        size = statistics.median([line['size'] for line in lines]) if lines else 10
                        blocks = paragraph_lines(lines, size)
                        for block in blocks:
                            x0, y0, x1, y1 = block['bbox']
                            uncertain_geometry = (page.rotation or tuple(page.bbox[:2]) != (0, 0) or
                                                  (page.cropbox and tuple(page.cropbox) != tuple(page.mediabox)))
                            block['bbox'] = None if uncertain_geometry else [x0 / page.width, y0 / page.height, x1 / page.width, y1 / page.height]
                        cache['pages'][str(number)] = page_blocks(number, blocks)
                        page.close()
            except Exception:
                # Page text remains useful when geometry extraction is unavailable.
                try:
                    pdf = PdfReader(path)
                    cache['page_count'] = len(pdf.pages)
                    for number in missing:
                        if number <= len(pdf.pages):
                            cache['pages'][str(number)] = page_blocks(number, [], pdf.pages[number - 1].extract_text() or '')
                except Exception as exc:
                    raise HTTPException(422, '该 PDF 的原文暂时无法提取，请检查文件后重试') from exc
            if file_revision(path) != revision:
                raise HTTPException(409, '提取原文期间文件已变化，请重试')
            output = _cache_path(directory, document_id)
            try:
                output.parent.mkdir(exist_ok=True)
                atomic_json(output, cache)
            except OSError as exc:
                raise HTTPException(503, '原文坐标缓存暂时无法保存，请检查磁盘后重试；已有文件未删除') from exc
        return [dict(block, document_id=document_id, document_name=name, source_revision=revision)
                for page in wanted for block in cache['pages'].get(str(page), [])]


def _tokens(text, limit=None):
    value = unicodedata.normalize('NFKC', text).casefold()
    words = re.findall(r'[a-z0-9][a-z0-9_-]{2,}', value)
    for part in re.findall(r'[\u3400-\u9fff]+', value):
        words.extend(part[i:i + 2] for i in range(len(part) - 1))
    return set(words[:limit])


def prepare_sources(directory, meta, body):
    """At most six PDF pages, paper-local lexical retrieval, bounded quotations."""
    _, count, _ = document_source(directory, meta, body.document_id)
    if body.page > count:
        raise HTTPException(422, '当前页码超出来源 PDF 范围')
    current = load_document_pages(directory, meta, body.document_id, [body.page])
    if body.selection and normalized_text(body.selection) not in normalized_text(' '.join(b['text'] for b in current)):
        raise HTTPException(422, '所选文本无法与当前页原文匹配，请重新选择或清除选区后提问')
    tokens = _tokens(body.message + ' ' + body.selection, 80)
    chosen = {(body.document_id, body.page)}
    for page in (body.page - 1, body.page + 1):
        if 1 <= page <= count:
            chosen.add((body.document_id, page))
    scored, budget = [], 4_000_000
    for doc in ['main'] + [item['id'] for item in meta.get('supplements', [])]:
        try:
            path, pages, _ = document_source(directory, meta, doc)
        except HTTPException:
            continue
        source = Path(directory) / 'text.txt' if doc == 'main' else path.with_suffix('.txt')
        if budget <= 0 or not source.is_file():
            continue
        with source.open(encoding='utf-8', errors='replace') as stream:
            text = stream.read(min(budget, 2_000_000))
        budget -= len(text)
        matches = list(re.finditer(r'\[Page (\d+)\]', text))
        for index, match in enumerate(matches):
            page = int(match[1])
            if (doc, page) in chosen or not 1 <= page <= pages:
                continue
            excerpt = text[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(text)]
            score = len(tokens.intersection(_tokens(excerpt)))
            if score:
                scored.append((score, doc, page))
    for _, doc, page in sorted(scored, reverse=True)[:3]:
        chosen.add((doc, page))
    blocks = list(current)
    for doc, page in sorted(chosen):
        if (doc, page) != (body.document_id, body.page):
            blocks.extend(load_document_pages(directory, meta, doc, [page]))
    blocks.sort(key=lambda b: (b['document_id'] == body.document_id and b['page'] == body.page,
                               len(tokens.intersection(_tokens(b['text'])))), reverse=True)
    sources, size = [], 0
    for block in blocks:
        if len(sources) >= 8 or size + len(block['text']) > SOURCE_CHARS:
            continue
        size += len(block['text'])
        sources.append({'id': f'S{len(sources) + 1}', 'label': f"{block['document_name']} · 第 {block['page']} 页",
                        'document_id': block['document_id'], 'document_name': block['document_name'],
                        'page': block['page'], 'quote': block['text'], 'bbox': block['bbox'],
                        'source_revision': block['source_revision'], 'block_id': block['id']})
    if not sources:
        raise HTTPException(422, '当前材料没有可读取的原文文本，暂不能据原文回答；扫描件需要文本识别后再试')
    return sources


def validate_source_snapshot(directory, meta, sources):
    checked = set()
    for source in sources:
        document_id = source['document_id']
        if document_id in checked:
            continue
        path, _, _ = document_source(directory, meta, document_id)
        if file_revision(path) != source['source_revision']:
            raise HTTPException(409, '来源文件已变化，这条回答引用的是旧版本，请重新提问')
        checked.add(document_id)


def verified_source(directory, meta, source):
    validate_source_snapshot(directory, meta, [source])
    blocks = load_document_pages(directory, meta, source['document_id'], [source['page']])
    block = next((b for b in blocks if b['id'] == source['block_id']), None)
    if block is None or normalized_text(source['quote']) not in normalized_text(block['text']):
        raise HTTPException(409, '该引用无法与原文缓存匹配，请重新提问')
    validate_source_snapshot(directory, meta, [source])
    return {**source, 'bbox': block['bbox']}
