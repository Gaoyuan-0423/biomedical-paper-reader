"""Local phrase search over saved notes and revision-bound PDF text blocks."""
import hashlib
import unicodedata

from fastapi import HTTPException

from chat_sources import document_source, file_revision, load_document_pages


def _normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if not c.isspace())


def _snippet(text, query):
    """Keep a real source excerpt; do not return generated or HTML-highlighted text."""
    normalized, positions = [], []
    for index, char in enumerate(text):
        chars = _normalized(char)
        normalized.extend(chars)
        positions.extend([index] * len(chars))
    position = ''.join(normalized).find(query)
    # Whole-string NFKC may compose combining characters across codepoints.
    # That rare match still returns actual text, never an invented quotation.
    at = positions[position] if position >= 0 and positions else 0
    start = max(0, at - 80)
    end = min(len(text), max(start + 320, at + len(query) + 80))
    return ('…' if start else '') + text[start:end] + ('…' if end < len(text) else '')


def search_library(entries, query, scope='all', offset=0, limit=20):
    """Search immutable metadata snapshots; each PDF is checked against its bytes.

    entries contains (paper_directory, metadata). PDF text caches are reused or
    built with the existing lightweight extractor. A damaged cache is reported,
    not replaced. Pagination stops after one extra match, with no hidden global
    result limit; warnings describe only the material visited for this page.
    """
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 200:
        raise HTTPException(422, '请输入 2–200 个字符的搜索词')
    needle = _normalized(query.strip())
    if len(needle) < 2:
        raise HTTPException(422, '请至少输入两个非空白字符')
    if scope not in ('all', 'pdf', 'notes'):
        raise HTTPException(422, '无效的搜索范围')
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 20:
        raise HTTPException(422, '无效的搜索页码')
    items, warnings, matched, warning_count = [], [], 0, 0

    def warn(meta, document_id, message):
        nonlocal warning_count
        warning_count += 1
        if len(warnings) < 20:
            warnings.append({'paper_id': meta['id'], 'paper_title': meta.get('title', ''),
                             'document_id': document_id, 'message': message})

    def add(result):
        nonlocal matched
        matched += 1
        if matched > offset:
            items.append(result)
        return len(items) > limit

    def response():
        more = len(items) > limit
        return {'items': items[:limit], 'has_more': more, 'offset': offset,
                'next_offset': offset + len(items[:limit]), 'warnings': warnings,
                'warning_count': warning_count}

    for directory, meta in entries:
        common = {'paper_id': meta['id'], 'paper_title': str(meta.get('title') or '未命名文献')}
        if scope in ('all', 'notes'):
            notes = meta.get('notes') or ''
            if isinstance(notes, str) and needle in _normalized(notes):
                if add({**common, 'kind': 'notes', 'document_id': None, 'source_name': '已保存笔记',
                        'page': None, 'snippet': _snippet(notes, needle), 'source_revision': None,
                        'block_id': None, 'source': None,
                        'notes_revision': hashlib.sha256(notes.encode()).hexdigest()}):
                    return response()
        if scope == 'notes':
            continue
        documents = (['main'] if meta.get('has_file') else []) + [s['id'] for s in meta.get('supplements', [])]
        for document_id in documents:
            try:
                path, count, name = document_source(directory, meta, document_id)
                if not 1 <= count <= 600:
                    raise HTTPException(409, 'PDF 页数信息无效，请重新导入材料')
                blocks = load_document_pages(directory, meta, document_id, list(range(1, count + 1)))
                if not blocks:
                    warn(meta, document_id, '没有可提取的文字；扫描件需要先完成文字识别')
                    continue
                if file_revision(path) != blocks[0]['source_revision']:
                    raise HTTPException(409, '搜索期间 PDF 已变化，请重新搜索')
            except HTTPException as exc:
                warn(meta, document_id, str(exc.detail))
                continue
            except (OSError, ValueError, TypeError):
                warn(meta, document_id, '材料暂时无法读取，请检查文件后重试')
                continue
            pages = {}
            for block in blocks:
                pages.setdefault(block['page'], []).append(block)
            for page_blocks in pages.values():
                hits = [(block, block['text']) for block in page_blocks if needle in _normalized(block['text'])]
                if not hits and len(page_blocks) > 1:
                    # A sentence can cross extraction/chunk boundaries. Anchor
                    # such a hit to its first original block, keeping that exact
                    # block as the independently verifiable navigation source.
                    joined = '\n'.join(block['text'] for block in page_blocks)
                    position = _normalized(joined).find(needle)
                    if position >= 0:
                        consumed = 0
                        for block in page_blocks:
                            consumed += len(_normalized(block['text']))
                            if position < consumed:
                                hits.append((block, joined))
                                break
                for block, snippet_text in hits:
                    source = {'document_id': document_id, 'document_name': name, 'page': block['page'],
                              'source_revision': block['source_revision'], 'block_id': block['id'],
                              'quote': block['text'], 'bbox': block['bbox']}
                    if add({**common, 'kind': 'pdf', 'document_id': document_id, 'source_name': name,
                            'page': block['page'], 'snippet': _snippet(snippet_text, needle),
                            'source_revision': block['source_revision'], 'block_id': block['id'],
                            'source': source}):
                        return response()
    return response()
