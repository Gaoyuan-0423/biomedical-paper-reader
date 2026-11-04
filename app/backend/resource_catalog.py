"""Local resource filtering and portable table exports; never queries GEO."""
import csv
import io
import unicodedata

from paper_resources import resource_accessions


SEARCH_FIELDS = ('title', 'paper_title', 'paper_doi', 'repository', 'accession', 'url',
                 'purpose', 'identifiers', 'conditions', 'sample_details', 'organism', 'tissue',
                 'disease', 'modality', 'platform', 'sample_groups', 'sample_count', 'notes', 'quote')
CSV_FIELDS = (
    ('paper_title', '论文标题'), ('paper_doi', '论文 DOI'), ('paper_id', '论文 ID'), ('id', '资源 ID'),
    ('title', '资源名称'), ('kind', '资源类型'), ('purpose', '用途'), ('role', '与本文关系'),
    ('repository', '仓库'), ('identifiers', '编号／链接'), ('accession', '兼容编号'), ('url', '资源链接'),
    ('organism', '物种／研究对象'), ('tissue', '组织／部位'), ('disease', '疾病／状态'),
    ('modality', '技术类型'), ('platform', '平台'), ('sample_groups', '论文实验分组'),
    ('sample_count', '论文实际样本量'), ('sample_details', '样品／材料原始说明'),
    ('conditions', '影响使用的条件'), ('notes', '备注'), ('source_origin', '整理来源'),
    ('source_name', '原文文件'), ('document_id', '原文文件 ID'), ('page', 'PDF 页码'),
    ('quote', '原文摘录'), ('source_state', '原文可用情况'),
)
_KINDS = {'dataset': '数据集', 'code': '代码', 'other': '其他资源'}
_ROLES = {'generated': '本文产生', 'reused': '复用已有资源', 'implementation': '代码实现', 'unknown': '未记录'}
_AUTO_NOTICES = {'来自精读笔记自动整理。', '来自精读笔记自动整理，尚未核对原文。'}
_OLD_SNIPPET = '来自精读笔记自动整理，尚未核对原文。笔记片段：\n'


def _normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).split()).casefold()


def filter_resources(entries, q='', organism='', tissue='', disease='', modality='', platform=''):
    """All filters are ANDed, using normalized case-insensitive substrings.

    Records and their existing cross-paper links remain unchanged. Callers build
    those links before filtering so matching one row does not hide its relations.
    """
    query = _normalized(q)
    filters = {name: _normalized(value) for name, value in (
        ('organism', organism), ('tissue', tissue), ('disease', disease),
        ('modality', modality), ('platform', platform)) if _normalized(value)}
    return [record for record in entries
            if (not query or query in _normalized(' '.join(str(record.get(f) or '') for f in SEARCH_FIELDS)))
            and all(value in _normalized(record.get(field)) for field, value in filters.items())]


def _spreadsheet_text(value):
    text = ('' if value is None else str(value)).replace('\x00', '')
    # Formula prefixes can be hidden behind whitespace/control characters.
    # Preserve the original text and use Excel's literal-text prefix instead.
    probe = unicodedata.normalize('NFKC', text).lstrip('\ufeff \t\r\n\v\f')
    if probe.startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')):
        text = "'" + text
    return text


def resource_csv(entries):
    """Export saved per-paper facts, excluding external GEO caches and status."""
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\r\n')
    writer.writerow([label for _, label in CSV_FIELDS])
    for record in entries:
        row = dict(record)
        notes = str(record.get('notes') or '')
        automatic = bool(record.get('auto_extraction')) or notes in _AUTO_NOTICES or notes.startswith(_OLD_SNIPPET)
        row['source_origin'] = '精读笔记自动整理' if automatic else '资源记录'
        row['notes'] = '' if notes in _AUTO_NOTICES or notes.startswith(_OLD_SNIPPET) else notes
        row['kind'] = _KINDS.get(record.get('kind'), '其他资源')
        row['role'] = _ROLES.get(record.get('role'), '未记录')
        row['source_state'] = ('未关联原文' if not record.get('document_id') else
                               '来源已移除或页码失效' if record.get('source_available') is False else
                               '来源文件已更新' if record.get('source_changed') else '可定位原文')
        writer.writerow([_spreadsheet_text(row.get(field)) for field, _ in CSV_FIELDS])
    return output.getvalue().encode('utf-8-sig')


def accession_text(entries):
    """Only explicit supported accessions from identifier fields or known URLs."""
    values = sorted({value for record in entries for value in resource_accessions(record)})
    return (''.join(value + '\n' for value in values)).encode('utf-8')
