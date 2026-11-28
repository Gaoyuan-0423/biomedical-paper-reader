"""Extract attributable card excerpts from a paper's saved Markdown, without generating text."""
import re

from markdown_it import MarkdownIt


SECTIONS = (
    ('background', '研究背景与问题'),
    ('design', '研究对象与设计'),
    ('methods', '研究方法'),
    ('results', '关键结果与数值'),
    ('mechanism', '机制与证据'),
    ('limits', '研究局限'),
    ('implications', '课题启示'),
    ('next', '待核问题与下一步'),
)

# Recognize structural labels in the deep/quick report templates and common
# user-authored headings. Ordinary mentions in prose do not classify a passage.
LABELS = {
    'background': ('研究背景与问题', '研究背景', '背景', '研究概述', '已有认识', '未解问题',
                   '研究问题', '研究目标与动机', 'Introduction', 'Background', 'Research question'),
    'design': ('研究对象与设计', '研究对象', '研究设计', '研究对象与类型', '研究材料与设计',
               '研究对象与总体设计', '总体设计', '本文切入点', 'Study design', 'Population'),
    'methods': ('研究方法', '方法', '方法与目的', '实验方法', '技术路线', 'Methods', 'Methodology'),
    'results': ('关键结果与数值', '关键结果', '关键数值', '研究结果', '实验设计与关键结果',
                '总体设计与主要发现', '主要发现与作者解释', '决定性结果与解释', '主要发现', '结果',
                'Results', 'Findings', 'Key findings'),
    'mechanism': ('机制与证据', '机制', '机制解释', '核心模型与贡献', '逻辑解读', '证据链',
                  '全文逻辑路线图', '机制与证据链', 'Mechanism', 'Evidence'),
    'limits': ('研究局限', '局限', '主要局限', '关键局限', '证据边界', 'Limitations', 'Limitations and caveats'),
    'implications': ('课题启示', '对课题的启示', '对我课题的启示', '适用场景', '值得迁移的研究设计',
                     '课题借鉴', '启示', 'Implications'),
    'next': ('待核问题与下一步', '待核问题', '下一步', '下一步优先回看', '延伸方向', '未来方向',
             '后续研究', '待验证问题', 'Open questions', 'Next steps', 'Future directions'),
    'conclusion': ('一句话核心结论', '一句话发现', '一句话结论', '核心结论', '结论',
                   'Conclusion', 'Conclusions', 'Takeaway'),
    'provisional': ('核心问题与暂定发现', '暂定发现'),
}
KEYS = {name.casefold(): key for key, names in LABELS.items() for name in names}
MARKDOWN = MarkdownIt('commonmark').enable('table')
CITATION = re.compile(r'note-source:([0-9a-f]{32}):(N[1-9][0-9]{0,4})')
BARE_MARKER = re.compile(r'\[N\d+(?:\s*[-–—]\s*N?\d+)?\]')
NUMBER_PREFIX = re.compile(r'^\s*(?:[（(]?[一二三四五六七八九十\d]+[)）.、．]\s*)+')
FIGURE_HEADING = re.compile(r'^(?:supplement(?:ary)?\s+|补充?|附)?(?:fig(?:ure)?\.?|图|表|table)\s*s?\d+', re.I)
CONCLUSIONS = ('conclusion', 'provisional')
SECTION_LIMIT = 700


def label_key(label):
    cleaned = NUMBER_PREFIX.sub('', label).strip(' :：').casefold()
    cleaned = re.sub(r'\s*[（(][^()（）]*[)）]$', '', cleaned).strip()
    return KEYS.get(cleaned)


def excerpt(text, limit):
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def inline_lines(children):
    """Collect visible inline text and bound citations, without dereferencing links or images."""
    lines = []
    words, citations = [], []
    label = None
    for i, token in enumerate(children):
        if token.type == 'strong_open' and not ''.join(words).strip() and i + 2 < len(children):
            value, end = children[i + 1:i + 3]
            if value.type == 'text' and end.type == 'strong_close':
                label = value.content
        if token.type == 'link_open':
            match = CITATION.fullmatch(token.attrGet('href') or '')
            tail = children[i + 1:i + 3]
            if (match and len(tail) == 2 and tail[0].type == 'text' and
                    tail[0].content == match[2] and tail[1].type == 'link_close'):
                citations.append(match.groups())
        elif token.type in ('text', 'code_inline'):
            # Only discard a link label if it is part of a note-source link.
            if i and children[i - 1].type == 'link_open' and CITATION.fullmatch(children[i - 1].attrGet('href') or ''):
                continue
            words.append(token.content)
        elif token.type in ('softbreak', 'hardbreak'):
            lines.append((''.join(words), label, citations))
            words, citations, label = [], [], None
    lines.append((''.join(words), label, citations))
    return lines


def reading_card(directory, meta):
    notes = (meta.get('notes') or '').strip()
    mode = meta.get('notes_mode')
    card = {
        'has_notes': bool(notes), 'mode': (mode if mode in ('deep', 'quick') else 'notes') if notes else '',
        'incomplete': bool(notes and meta.get('notes_incomplete')),
        'stale': bool(notes and (meta.get('notes_stale') or
                      meta.get('notes_material_revision', meta.get('material_revision')) != meta.get('material_revision'))),
        'note_title': '', 'conclusion': '', 'conclusion_source': '', 'conclusion_citation': None,
        'abstract_excerpt': excerpt(' '.join((meta.get('abstract') or '').split()), 500),
        'note_excerpt': '', 'sections': [{'key': key, 'label': label, 'text': ''} for key, label in SECTIONS],
    }
    if not notes:
        return card

    sections = {section['key']: section for section in card['sections']}
    figure_text = {key: '' for key in sections}
    last_figure = {}
    headings = []
    active = None
    table_header = []
    table_row = None
    heading_level = 0
    quote_depth = 0
    in_header = False

    def append(key, value, refs=()):
        value = re.sub(r'[ \t]+', ' ', BARE_MARKER.sub('', value)).strip()
        if not value:
            return
        if not card['note_excerpt']:
            card['note_excerpt'] = excerpt(value, 360)
        if key in CONCLUSIONS and not card['conclusion'] and not any(FIGURE_HEADING.match(h[2]) for h in headings):
            card['conclusion'] = excerpt(value, 900)
            card['conclusion_source'] = 'quick_notes' if mode == 'quick' or key == 'provisional' else 'notes'
            # The ordinary note-source endpoint verifies the current note and PDF on click.
            for snapshot_id, sid in refs:
                if len(value) <= 900 and snapshot_id == meta.get('notes_source_snapshot'):
                    card['conclusion_citation'] = {'snapshot_id': snapshot_id, 'source_id': sid,
                                                   'notes_revision': meta.get('notes_revision', 0)}
                    break
        if key in sections:
            figure = next((title for _, _, title in reversed(headings) if FIGURE_HEADING.match(title)), '')
            if figure:
                if last_figure.get(key) != figure:
                    value = figure + ' · ' + value
                    last_figure[key] = figure
                figure_text[key] = excerpt((figure_text[key] + '\n' + value).strip(), SECTION_LIMIT)
            elif len(sections[key]['text']) < SECTION_LIMIT:
                sections[key]['text'] = excerpt((sections[key]['text'] + '\n' + value).strip(), SECTION_LIMIT)

    for token in MARKDOWN.parse(notes):
        kind = token.type
        if kind == 'blockquote_open':
            quote_depth += 1
        elif kind == 'blockquote_close':
            quote_depth -= 1
        elif quote_depth:
            continue
        elif kind == 'heading_open':
            heading_level = int(token.tag[1:])
        elif kind == 'heading_close':
            heading_level = 0
        elif kind == 'table_open':
            table_header = []
        elif kind == 'thead_open':
            in_header = True
        elif kind == 'thead_close':
            in_header = False
        elif kind == 'tr_open':
            table_row = []
        elif kind == 'tr_close':
            if in_header:
                table_header = table_row
            elif table_row:
                property_key = label_key(table_row[0])
                if property_key in sections and len(table_row) == 2:
                    append(property_key, table_row[0] + '：' + table_row[1])
                elif active in sections:
                    append(active, ' · '.join(f'{h}：{v}' if h else v
                                              for h, v in zip(table_header, table_row) if v))
            table_row = None
        elif kind == 'inline':
            lines = inline_lines(token.children or [])
            text = ' '.join(line.strip() for line, _, _ in lines).strip()
            if heading_level:
                while headings and headings[-1][0] >= heading_level:
                    headings.pop()
                active = label_key(text)
                if active in CONCLUSIONS and heading_level > 2:
                    active = None  # A result subsection can describe only one figure.
                if active is None and heading_level > 2 and headings:
                    active = headings[-1][1]
                headings.append((heading_level, active, text))
                if heading_level == 1 and not card['note_title'] and active is None:
                    card['note_title'] = excerpt(text, 350)
                continue
            if table_row is not None:
                table_row.append(text)
                continue
            # Markdown permits adjacent labelled lines inside one paragraph.
            # Merge ordinary wrapped lines before selecting a complete excerpt.
            pending, citations = [], []
            for line, bold_label, refs in lines:
                stripped = line.strip()
                prefix = bold_label or re.split(r'[:：]', stripped, 1)[0]
                key = label_key(prefix) if bold_label or re.search(r'[:：]', stripped) else None
                if key or (bold_label and bold_label.endswith(('：', ':'))):
                    append(active, ' '.join(pending), citations)
                    pending, citations = [], []
                    active = key or (headings[-1][1] if headings else None)
                    if key in CONCLUSIONS and headings and headings[-1][0] > 2:
                        active = None
                    remainder = stripped[len(prefix.strip()):].lstrip('：: ').strip()
                    if key in CONCLUSIONS or (key and not remainder):
                        stripped = remainder
                if stripped:
                    pending.append(stripped)
                citations.extend(refs)
            append(active, ' '.join(pending), citations)
    for key, section in sections.items():
        if figure_text[key] and len(section['text']) < SECTION_LIMIT:
            section['text'] = excerpt((section['text'] + '\n' + figure_text[key]).strip(), SECTION_LIMIT)
    return card
