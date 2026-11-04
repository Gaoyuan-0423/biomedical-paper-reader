"""Portable, deterministic exports of human evidence and research-resource records."""
from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import quote, urlsplit


_STATUS = {'pending': '待核查', 'verified': '已核查', 'rejected': '不支持该判断'}
_KINDS = {'dataset': '数据集', 'code': '代码', 'other': '其他资源'}
_ROLES = {'generated': '本文产生', 'reused': '复用已有数据', 'implementation': '方法实现', 'unknown': '关系待确认'}
_LEGACY_RESOURCE_NOTICE = '来自精读笔记自动整理，尚未核对原文。笔记片段：\n'
_RESOURCE_NOTICES = {'来自精读笔记自动整理，尚未核对原文。', '来自精读笔记自动整理。'}
_GENERATED = re.compile(
    r'\n\n<!-- bioreader-research-appendix:v1 sha256=([0-9a-f]{64}) -->\n'
    r'(.*?)\n<!-- /bioreader-research-appendix:v1 -->\n\Z', re.S)


def _text(value):
    """Treat fields as literal text, including HTML, Markdown and figure: images."""
    raw = str(value if value is not None else '').replace('\r\n', '\n').replace('\r', '\n')
    raw = ''.join(c for c in raw if c in '\n\t' or ord(c) >= 32)
    escaped = html.escape(raw, quote=True)
    return re.sub(r'([\\`*_{}\[\]()#+.!|>~\-])', r'\\\1', escaped)


def _inline(value):
    return ' '.join(_text(value).split())


def _safe_url(value):
    value = str(value or '').strip()
    if not value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        return ''
    try:
        parsed = urlsplit(value)
        if (parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None or '\\' in value):
            return ''
        parsed.port  # Reject malformed ports before the link reaches any renderer.
    except ValueError:
        return ''
    # Parentheses, brackets, quotes, angle brackets and backticks cannot end a
    # Markdown destination or become raw HTML. Existing percent escapes survive.
    return quote(value, safe='/:?#&=%+;,$-._~!@')


def _source(record):
    document = record.get('document_id')
    name = record.get('source_name')
    if not document:
        return '未关联原文'
    if not name:
        name = '正文' if document == 'main' else '补充材料（文件名未记录）'
    value = _inline(name)
    page = record.get('page')
    if isinstance(page, int) and not isinstance(page, bool) and page > 0:
        value += f' · PDF 第 {page} 页'
    else:
        value += ' · 页码未记录'
    if record.get('figure_id'):
        value += ' · 图表记录 ' + _inline(record['figure_id'])
    return value


def _record_details(record, include_status=True):
    status = record.get('status', 'pending')
    unavailable = record.get('source_available') is False
    changed = bool(record.get('source_changed'))
    if status == 'verified' and (unavailable or changed):
        status = 'pending'
    lines = (['- 核查状态：' + _STATUS.get(status, '待核查')] if include_status else [])
    lines.append('- 原文位置：' + _source(record))
    if unavailable:
        lines.append('- 来源状态：来源已移除或页码失效，' + ('需要重新关联并核查。' if include_status else '原文位置不可用。'))
    if changed:
        lines.append('- 来源状态：来源文件已更新，' + ('需要重新核查。' if include_status else '已记录内容可能对应旧版本。'))
    if record.get('quote'):
        lines.extend(['', '原文摘录或图表观察：', ''])
        lines.extend('> ' + line for line in _text(record['quote']).split('\n'))
    return lines


def _literal_block(label, value):
    """Keep multiline resource cells readable without interpreting their markup."""
    return ['', '**' + label + '：**', '', *('> ' + line for line in _text(value).split('\n'))]


def _without_generated_appendix(notes):
    """Replace only an unedited, digest-matching generated terminal appendix.

    A similarly named user heading, manually edited block, or nonterminal block
    is preserved. The two separator newlines belong to the generated suffix.
    """
    starts = list(re.finditer(r'\n\n<!-- bioreader-research-appendix:v1 sha256=', notes))
    for start in reversed(starts):
        match = _GENERATED.match(notes, start.start())
        if match and hashlib.sha256(match[2].encode('utf-8')).hexdigest() == match[1]:
            return notes[:match.start()]
    return notes


def append_research_appendix(notes, evidence, resources):
    """Append export-only records without changing source notes or input objects.

    Pass public records so current source availability is reflected in the
    export. HTTP/HTTPS links are retained; arbitrary record text is literal.
    """
    notes = _without_generated_appendix(notes)
    if not evidence and not resources:
        return notes
    sections = ['## 阅读核查与资源记录', '', '以下为阅读者保存的核查与资源记录。']
    if evidence:
        sections.extend(['', '### 关键证据'])
        for index, record in enumerate(evidence, 1):
            sections.extend(['', f'#### 证据 {index}', '', '**判断：** ' + _inline(record.get('claim') or '判断未记录'), ''])
            sections.extend(_record_details(record))
    if resources:
        sections.extend(['', '### 数据与代码'])
        for index, record in enumerate(resources, 1):
            sections.extend(['', f'#### 资源 {index} · ' + _inline(record.get('title') or '未命名资源'), '',
                             '- 类型：' + _KINDS.get(record.get('kind'), '其他资源'),
                             '- 与本文关系：' + _ROLES.get(record.get('role'), '关系待确认')])
            for field, label in [('purpose', '用途'), ('identifiers', '编号／链接'),
                                 ('conditions', '使用条件'), ('sample_details', '样品明细')]:
                if record.get(field):
                    sections.extend(_literal_block(label, record[field]))
            sections.append('')
            for field, label in [('repository', '仓库'), ('accession', '编号'), ('organism', '研究对象'),
                                 ('tissue', '组织'), ('disease', '疾病／状态'), ('modality', '技术类型'),
                                 ('platform', '平台'), ('sample_groups', '论文实验分组'),
                                 ('sample_count', '论文实际样本量')]:
                if record.get(field):
                    sections.append('- ' + label + '：' + _inline(record[field]))
            if record.get('url'):
                link = _safe_url(record['url'])
                sections.append('- 链接：' + ('[打开资源](' + link + ')' if link else '未导出（仅支持有效的 HTTP/HTTPS 地址）'))
            sections.extend(_record_details(record, include_status=False))
            notes_value = record.get('notes') or ''
            if isinstance(notes_value, str) and (notes_value.startswith(_LEGACY_RESOURCE_NOTICE) or notes_value in _RESOURCE_NOTICES):
                sections.extend(['', '- 整理来源：精读笔记自动整理。'])
            elif notes_value:
                sections.extend(_literal_block('备注', notes_value))
    body = '\n'.join(sections)
    digest = hashlib.sha256(body.encode('utf-8')).hexdigest()
    return (notes + '\n\n<!-- bioreader-research-appendix:v1 sha256=' + digest + ' -->\n' + body
            + '\n<!-- /bioreader-research-appendix:v1 -->\n')
