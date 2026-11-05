"""Immutable, generation-scoped original excerpts for verifiable reading notes."""
import hashlib
import html
import json
import re
from pathlib import Path
from html.parser import HTMLParser

from markdown_it import MarkdownIt
from markdown_it.rules_inline import link as markdown_link, image as markdown_image, html_inline

from fastapi import HTTPException
from chat_sources import (document_source, file_revision, load_document_pages, valid_bbox)
from reading_workflow import atomic_json

TOKEN = re.compile(r'\[(N[1-9][0-9]{0,4})\]')
LINK = re.compile(r'\[(N[1-9][0-9]{0,4})\]\(note-source:([0-9a-f]{32}):(N[1-9][0-9]{0,4})\)')
MAX_CHARS = 2_000_000
MAX_SOURCES = 10000
active_generations = {}  # Access only while holding storage_lock.


class _HTMLContext(HTMLParser):
    """Track literal HTML containers without interpreting their contents."""
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.tags = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.tags.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in self.tags:
            index = len(self.tags) - 1 - self.tags[::-1].index(tag)
            del self.tags[index:]


def _markdown_spans(text):
    """Return original-source offsets; parse Markdown without rewriting formatting.

    Block tokens exclude code, HTML blocks and reference definitions. An inline
    rule records only literal source markers, after ruling out reference links.
    Position mapping uses exact marker text inside the parser's source-line map;
    container prefixes and escaped table pipes therefore need no reconstruction.
    """
    markdown = MarkdownIt('commonmark').enable('table')
    prose, private = [], []
    # CommonMark normalizes CR/LF only; Unicode paragraph separators remain
    # characters inside a line and must not shift source-position maps.
    offsets = [0] + [match.end() for match in re.finditer(r'\r\n?|\n', text)]
    if offsets[-1] < len(text):
        offsets.append(len(text))
    cursors = {}
    html_context = _HTMLContext()

    def marker(state, silent):
        if state.linkLevel or state.env.get('_note_nested') or html_context.tags:
            return False
        start = state.pos
        match = LINK.match(state.src, start) or TOKEN.match(state.src, start)
        if not match or start and state.src[start - 1] in '!\\':
            return False
        # A shortcut/full/collapsed reference takes precedence over [N1].
        linked = markdown_link(state, True)
        end = state.pos
        state.pos = start
        if linked and (not LINK.fullmatch(match[0]) or end != match.end()):
            return False
        if not linked and state.src[match.end():match.end() + 1] in ('[', '('):
            return False
        if not silent:
            state.env['_note_markers'].add((start, match.end()))
            state.pending += match[0]
        state.pos = match.end()
        return True

    def wrapped_link(rule):
        def wrapped(state, silent):
            start, token_start = state.pos, len(state.tokens)
            state.env['_note_nested'] = state.env.get('_note_nested', 0) + 1
            try:
                matched = rule(state, silent)
            finally:
                state.env['_note_nested'] -= 1
            if matched and not silent:
                tokens = state.tokens[token_start:]
                if any(str(token.attrGet('href') or token.attrGet('src') or '').lower().startswith('note-source:') for token in tokens):
                    label = ''.join(token.content for token in tokens if token.type in ('text', 'code_inline', 'image'))
                    state.env['_note_private'].append((start, state.pos, label))
            return matched
        return wrapped

    def track_html(state, silent):
        start = state.pos
        matched = html_inline(state, silent)
        if matched and not silent:
            html_context.feed(state.src[start:state.pos])
        return matched

    def inlines(state):
        for token in state.tokens:
            if token.type == 'html_block':
                html_context.feed(token.content)
            if token.type != 'inline' or not token.map:
                continue
            env = {**state.env, '_note_markers': set(), '_note_private': []}
            token.children = []
            markdown.inline.parse(token.content, markdown, env, token.children)
            first, last = token.map
            lo, hi = offsets[first], offsets[min(last, len(offsets) - 1)]
            cursor = cursors.get((first, last), lo)
            # Map *all* markers in order, including literal ones, so repeated
            # labels in images/code cannot steal a later prose marker's offset.
            for match in re.finditer(LINK.pattern + '|' + TOKEN.pattern, token.content):
                position = text.find(match[0], cursor, hi)
                if position < 0:
                    continue  # Ambiguous positions never acquire a citation.
                cursor = position + len(match[0])
                if match.span() in env['_note_markers']:
                    prose.append((position, cursor))
            cursors[(first, last)] = cursor
            private_cursor = lo
            for a, b, label in env['_note_private']:
                raw = token.content[a:b]
                position = text.find(raw, private_cursor, hi)
                if position >= 0:
                    private_cursor = position + len(raw)
                    private.append((position, private_cursor, label))

    markdown.inline.ruler.before('link', 'note_marker', marker)
    markdown.inline.ruler.at('link', wrapped_link(markdown_link))
    markdown.inline.ruler.at('image', wrapped_link(markdown_image))
    markdown.inline.ruler.at('html_inline', track_html)
    markdown.core.ruler.at('inline', inlines)
    markdown.parse(text)
    return sorted(set(prose)), private


def prose_transform(text, transform):
    """Transform only source markers parsed as prose, retaining every other byte."""
    output, position = [], 0
    spans, _ = _markdown_spans(text)
    for start, end in spans:
        output.extend((text[position:start], transform(text[start:end])))
        position = end
    output.append(text[position:])
    return ''.join(output)


def _redact_private_targets(text):
    scheme = r'note-source(?::|&#(?:x0*3a|0*58);|&colon;)'
    def attribute(match):
        target = next(value for value in match.groups() if value is not None)
        return '' if html.unescape(target).lstrip().lower().startswith('note-source:') else match[0]
    # Decode HTML entities before checking attributes, including entities inside
    # the scheme itself. Preserve the surrounding element and its visible label.
    text = re.sub(r'''\s+(?:href|src)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))''',
                  attribute, text, flags=re.I)
    return re.sub(scheme + r"[^\s<>\[\]()\"']*", '原文来源不可用', text, flags=re.I)


def _strip_private_uris(text):
    """Exports never contain internal URI targets, including noncanonical ones."""
    _, private = _markdown_spans(text)
    outermost = []
    for start, end, label in sorted(private, key=lambda item: (item[0], -item[1])):
        if not outermost or start >= outermost[-1][1]:
            outermost.append((start, end, label))
    for start, end, label in reversed(outermost):
        literal = re.sub(r'([\\`*_{}\[\]()#+.!|>~\-])', r'\\\1', html.escape(label or '原文来源不可用', quote=True))
        text = text[:start] + literal + '〔原文来源不可用〕' + text[end:]
    # Also remove raw HTML attributes, inert examples, reference definitions and
    # malformed targets. These never confer evidence or add a source appendix.
    return _redact_private_targets(text)


def visible_links(notes):
    found = []
    def collect(part):
        found.extend(LINK.findall(part))
        return part
    prose_transform(notes, collect)
    return found


def notes_hash(notes):
    return hashlib.sha256(notes.encode('utf-8')).hexdigest()


def note_state(meta):
    return meta.get('notes_revision', 0), notes_hash(meta.get('notes', ''))


def _path(directory, snapshot_id):
    if not isinstance(snapshot_id, str) or not re.fullmatch(r'[0-9a-f]{32}', snapshot_id):
        raise HTTPException(404, '笔记来源版本不存在')
    return Path(directory) / 'note-sources' / (snapshot_id + '.json')


def load_snapshot(directory, snapshot_id):
    path = _path(directory, snapshot_id)
    if not path.is_file():
        raise HTTPException(404, '笔记来源快照不存在，旧笔记可以继续阅读')
    try:
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError()
        value = json.loads(path.read_text('utf-8'))
        if (not isinstance(value, dict) or value.get('version') != 1 or value.get('snapshot_id') != snapshot_id or
                type(value.get('base_notes_revision')) is not int or
                not isinstance(value.get('base_notes_hash'), str) or not re.fullmatch(r'[0-9a-f]{64}', value['base_notes_hash']) or
                type(value.get('material_revision')) is not int or not isinstance(value.get('sources'), list) or
                len(value['sources']) > MAX_SOURCES):
            raise ValueError()
        seen = set()
        for source in value['sources']:
            if (not isinstance(source, dict) or not isinstance(source.get('id'), str) or
                    not re.fullmatch(r'N[1-9][0-9]{0,4}', source['id']) or source['id'] in seen or
                    not isinstance(source.get('document_id'), str) or
                    source['document_id'] != 'main' and not re.fullmatch(r'[0-9a-f]{32}', source['document_id']) or
                    type(source.get('page')) is not int or not 1 <= source['page'] <= 600 or
                    not isinstance(source.get('quote'), str) or not 0 < len(source['quote']) <= 2400 or
                    not isinstance(source.get('block_id'), str) or not re.fullmatch(r'p\d+-b\d+', source['block_id']) or
                    not isinstance(source.get('source_revision'), str) or not re.fullmatch(r'[0-9a-f]{64}', source['source_revision']) or
                    not isinstance(source.get('document_name'), str) or not isinstance(source.get('label'), str) or
                    'bbox' not in source or source['bbox'] is not None and not valid_bbox(source['bbox'])):
                raise ValueError()
            seen.add(source['id'])
        return value
    except (ValueError, TypeError, KeyError, OSError, UnicodeError) as exc:
        raise HTTPException(409, '笔记来源快照无法读取，原文件已保留，请检查 note-sources 本地记录') from exc


def save_snapshot(directory, snapshot_id, meta, sources):
    path = _path(directory, snapshot_id)
    if path.exists():
        raise HTTPException(409, '笔记来源快照已存在，不能覆盖')
    revision, digest = note_state(meta)
    value = {'version': 1, 'snapshot_id': snapshot_id, 'base_notes_revision': revision,
             'base_notes_hash': digest, 'material_revision': meta['material_revision'], 'sources': sources}
    if len(json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')) > 20 * 1024 * 1024:
        raise HTTPException(413, '笔记来源快照超过容量限制，原笔记已保留，请减少一次提供的材料')
    try:
        path.parent.mkdir(exist_ok=True)
        atomic_json(path, value)
    except OSError as exc:
        raise HTTPException(503, '笔记来源无法保存，原笔记已保留，请检查磁盘后重试') from exc
    return value


def source_text(source):
    return f"[{source['id']}] {source['document_name']} · PDF 第 {source['page']} 页\n{source['quote']}"


def prepare_sources(directory, meta):
    """Reuse block extraction outside the global lock; retain actual file-local pages."""
    sources, sections, chars, offset, truncated = [], [], 0, 0, False
    documents = ['main'] + [item['id'] for item in meta.get('supplements', [])]
    for document_id in documents:
        _, count, name = document_source(directory, meta, document_id)
        blocks = load_document_pages(directory, meta, document_id, list(range(1, count + 1)))
        previous_page = None
        for block in blocks:
            if len(sources) >= MAX_SOURCES or chars + len(block['text']) > MAX_CHARS:
                truncated = True
                break
            source = {'id': f'N{len(sources) + 1}', 'document_id': document_id, 'document_name': name,
                      'page': block['page'], 'quote': block['text'], 'bbox': block['bbox'],
                      'block_id': block['id'], 'source_revision': block['source_revision'],
                      'label': f"{name} · PDF 第 {block['page']} 页"}
            if previous_page != block['page']:
                sections.append(f"[Page {offset + block['page']}] {name} 原文件第 {block['page']} 页")
                previous_page = block['page']
            sources.append(source)
            sections.append(source_text(source))
            chars += len(block['text'])
        offset += count
        if truncated:
            break
    if not sources:
        raise HTTPException(422, '没有可核验的原文文本，扫描 PDF 需要文本识别后再生成引用')
    return '\n\n'.join(sections), sources, truncated


def filter_source_tokens(text, allowed):
    return TOKEN.sub(lambda m: m[0] if m[1] in allowed else '', text)


def condensed_context(summary, sources, budget):
    """A summary alone cannot confer citations: reattach original, bounded quotes."""
    by_id = {source['id']: source for source in sources}
    preferred = list(dict.fromkeys(TOKEN.findall(summary)))
    candidates = [by_id[sid] for sid in preferred if sid in by_id]
    candidates.extend(source for source in sources if source['id'] not in preferred)
    selected, excerpts, used = [], [], 0
    allowance = max(1000, int(budget * .45))
    for source in candidates:
        room = allowance - used - len(source_text({**source, 'quote': ''})) - 4
        if room < 160:
            break
        excerpt = {**source, 'quote': source['quote'][:room]}
        rendered = source_text(excerpt)
        selected.append(excerpt)
        excerpts.append(rendered)
        used += len(rendered) + 2
    allowed = {source['id'] for source in selected}
    summary = filter_source_tokens(summary, allowed)
    lead = '以下压缩概览仅作线索，不是原文证据；只有后附原文块可以引用。\n'
    tail = '\n\n可核验原文块（仅限以下片段，未列出的内容不能给出来源编号）：\n' + '\n\n'.join(excerpts)
    return lead + summary[:max(0, budget - len(lead) - len(tail))] + tail, selected


def bind_citations(notes, snapshot):
    allowed = {source['id'] for source in snapshot['sources']}
    snapshot_id = snapshot['snapshot_id']
    # Model-written private links are untrusted. Rebuild only our exact allowed IDs.
    def bind(part):
        part = LINK.sub(lambda m: f'[{m[1]}]', part)
        return re.sub(r'(?<![!\\])\[(N[1-9][0-9]{0,4})\](?![\[(])',
                      lambda m: f'[{m[1]}](note-source:{snapshot_id}:{m[1]})' if m[1] in allowed else m[0], part)
    return prose_transform(notes, bind)


def partial_notes(directory, meta, notes, snapshot_id):
    snapshot = load_snapshot(directory, snapshot_id)
    base = (snapshot['base_notes_revision'], snapshot['base_notes_hash'])
    if note_state(meta) != base:
        identical = meta.get('notes_source_snapshot') == snapshot_id and bind_citations(notes, snapshot) == meta.get('notes')
        if not identical:
            raise HTTPException(409, '笔记已被编辑或重新生成，不能用较早的部分回答覆盖，请保留当前笔记')
    return bind_citations(notes, snapshot)


def current_sources(directory, meta):
    snapshot_id = meta.get('notes_source_snapshot', '')
    revision = meta.get('notes_revision', 0)
    links = visible_links(meta.get('notes', ''))
    result = {'notes_revision': revision, 'snapshot_id': snapshot_id, 'sources': [], 'unresolved_ids': []}
    if not snapshot_id:
        result['unresolved_ids'] = list(dict.fromkeys(sid for sid, _, _ in links))
        return result
    snapshot = load_snapshot(directory, snapshot_id)
    known = {source['id']: source for source in snapshot['sources']}
    checked, seen = {}, set()
    for label, generation, sid in links:
        if generation != snapshot_id or label != sid or sid not in known:
            result['unresolved_ids'].append(sid)
            continue
        if sid in seen:
            continue
        seen.add(sid)
        source = known[sid]
        doc = source['document_id']
        if doc not in checked:
            try:
                path, _, _ = document_source(directory, meta, doc)
                checked[doc] = file_revision(path)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
                checked[doc] = None
        available = checked[doc] is not None
        changed = available and checked[doc] != source['source_revision']
        result['sources'].append({**source, 'snapshot_id': snapshot_id, 'source_available': available,
                                  'source_changed': changed})
    result['unresolved_ids'] = list(dict.fromkeys(result['unresolved_ids']))
    return result


def lookup_current(directory, meta, snapshot_id, source_id, notes_revision):
    if notes_revision != meta.get('notes_revision', 0) or snapshot_id != meta.get('notes_source_snapshot', ''):
        raise HTTPException(409, '笔记版本已变化，请刷新笔记后再打开引用')
    if (source_id, snapshot_id, source_id) not in visible_links(meta.get('notes', '')):
        raise HTTPException(404, '当前笔记不再包含这条原文引用')
    snapshot = load_snapshot(directory, snapshot_id)
    source = next((s for s in snapshot['sources'] if s['id'] == source_id), None)
    if source is None:
        raise HTTPException(404, '这条原文引用不存在')
    return source


def portable_notes(directory, meta, notes):
    snapshot_id = meta.get('notes_source_snapshot', '')
    links = visible_links(notes)
    if not links:
        return _strip_private_uris(notes)
    snapshot = load_snapshot(directory, snapshot_id) if snapshot_id else {'sources': []}
    known = {source['id']: source for source in snapshot['sources']}
    used = {}
    def literal(value):
        return re.sub(r'([\\`*_{}\[\]()#+.!|>~\-])', r'\\\1', html.escape(str(value), quote=True))
    def replace(match):
        label, generation, sid = match.groups()
        if generation != snapshot_id or sid != label or sid not in known:
            return f'〔{label}：原文来源不可用〕'
        source = known[sid]
        used[sid] = source
        return f"〔{sid}：{literal(source['document_name'])} · PDF 第 {source['page']} 页〕"
    notes = prose_transform(notes, lambda part: LINK.sub(replace, part))
    notes = LINK.sub(lambda m: f'[{m[1]}]', notes)  # Inert examples never add provenance.
    notes = _strip_private_uris(notes)
    if used:
        appendix = ['\n\n## 笔记原文出处\n', '以下为生成时记录的原文片段，原文定位不等于结论已获人工核验。\n']
        for sid, source in used.items():
            warning = ''
            try:
                path, _, _ = document_source(directory, meta, source['document_id'])
                if file_revision(path) != source['source_revision']:
                    warning = '（来源文件已更新；以下保留旧版本摘录）'
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
                warning = '（来源文件已移除；以下保留历史摘录）'
            appendix.append(f"**{sid} · {literal(source['document_name'])} · PDF 第 {source['page']} 页**{warning}\n\n" +
                            '\n'.join('> ' + literal(line) for line in source['quote'].splitlines()) + '\n')
        notes += '\n'.join(appendix)
    return _redact_private_targets(notes)
