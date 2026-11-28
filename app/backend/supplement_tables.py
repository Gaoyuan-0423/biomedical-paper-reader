"""Read a single numeric cell from the current paper's original supplement."""
import csv
import hashlib
import io
import re
import unicodedata
import zipfile
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException
from openpyxl import load_workbook
from openpyxl.utils.datetime import CALENDAR_MAC_1904
from xlfmt import format_value

EXTENSIONS = {'.xlsx', '.csv', '.tsv'}
MAX_BYTES = 20 * 1024 * 1024
MAX_EXPANDED = 64 * 1024 * 1024
MAX_ROWS = 200_000
MAX_COLS = 512
MAX_CELLS = 3_000_000
MAX_FILES = 16
MAX_SHEETS = 64
TABLE_REF = re.compile(r'(?:supplement(?:ary|al)?\s+table|table|补充表|附表|表)\s*[_-]?\s*(s?\d+[a-z]?)(?![a-z0-9])', re.I)
METRIC = re.compile(r'(?<![a-z0-9_])(?:log2fc|logfc|avg_log2fc|log2foldchange|fdr|p[._ -]?val(?:ue)?(?:[._]adj)?|padj)(?![a-z0-9_])', re.I)
NUMBER = re.compile(r'[<>≤≥]?\s*[+−-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?\s*%?')
IDENTIFIERS = {'id', 'gene', 'genes', 'geneid', 'genename', 'symbol', 'genesymbol',
               'protein', 'proteinid', 'accession', 'sample', 'sampleid', '基因', '基因名', '基因名称', '编号'}


class TableError(ValueError):
    pass


def norm(value):
    return unicodedata.normalize('NFKC', str(value)).strip().casefold()


def mentioned(value, question):
    """Keep EGR1 distinct from EGR10, and the field pval distinct from pval_adj."""
    if not value or value not in question:
        return False
    return re.search(term_pattern(value), question, flags=re.ASCII) is not None


def term_pattern(value):
    return r'(?<![\w-])(?<![\w]\.)' + re.escape(value) + r'(?![\w-]|\.\w)'


class TableRow(list):
    """Raw strings for matching and verification, with lazy Excel display formatting."""

    @classmethod
    def from_excel(cls, cells, date1904):
        row = cls('' if cell.value is None else str(cell.value) for cell in cells)
        row.excel_cells = cells
        row.date1904 = date1904
        return row

    def display(self, index):
        if not 0 <= index < len(self):
            return ''
        cells = getattr(self, 'excel_cells', None)
        if cells is None:
            return self[index]  # CSV and TSV carry their own display text.
        cell = cells[index]
        if cell.value is None:
            return ''
        if len(cell.number_format or '') > 255:
            raise TableError('Excel 单元格的数字格式过长，无法核实其显示值')
        try:
            return str(format_value(cell.value, cell.number_format, date1904=self.date1904))
        except (ValueError, TypeError, ArithmeticError) as exc:
            raise TableError('无法核实 Excel 单元格的数字显示格式') from exc


@contextmanager
def open_sheets(content, extension):
    """Yield original worksheet names and one-based rows, including empty rows."""
    if not content or len(content) > MAX_BYTES:
        raise TableError('文件为空或超过 20 MB 的单表查询上限')
    try:
        if extension == '.xlsx':
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if len(archive.infolist()) > 4000 or sum(item.file_size for item in archive.infolist()) > MAX_EXPANDED:
                    raise TableError('工作簿解压后超过查询上限')
            book = load_workbook(io.BytesIO(content), read_only=True, data_only=True, keep_links=False)
            try:
                if not 1 <= len(book.worksheets) <= MAX_SHEETS:
                    raise TableError('工作表数量超出查询范围')
                def sheet_rows(sheet):
                    # Export tools sometimes record A1:A1 for a much larger table.
                    sheet.reset_dimensions()
                    return (TableRow.from_excel(row, book.epoch == CALENDAR_MAC_1904)
                            for row in sheet.iter_rows())
                yield [(sheet.title, sheet_rows(sheet)) for sheet in book.worksheets]
            finally:
                book.close()
        else:
            with io.TextIOWrapper(io.BytesIO(content), encoding='utf-8-sig', newline='') as stream:
                yield [(extension[1:].upper(), csv.reader(stream, delimiter='\t' if extension == '.tsv' else ',', strict=True))]
    except TableError:
        raise
    except Exception as exc:
        raise TableError('表格无法读取，请检查工作簿是否完整或 CSV/TSV 是否使用 UTF-8 编码') from exc


def checked_rows(rows, budget):
    for number, raw in enumerate(rows, 1):
        budget[0] -= max(1, len(raw))
        if number > MAX_ROWS or len(raw) > MAX_COLS or budget[0] < 0:
            raise TableError('表格大小超出查询范围，无法核实唯一匹配')
        yield number, raw if isinstance(raw, TableRow) else TableRow(raw)


def validate_upload(filename, content):
    with open_sheets(content, Path(filename).suffix.lower()) as sheets:
        # Validate the whole input before registering a local table.
        budget = [MAX_CELLS]
        any_data = False
        for _, rows in sheets:
            for _, cells in checked_rows(rows, budget):
                if any(cells):
                    any_data = True
        if not any_data:
            raise TableError('表格为空')


def table_source_path(directory, meta, source):
    record = next((r for r in meta.get('supplement_files', []) if r.get('id') == source['document_id']), None)
    if not record:
        raise HTTPException(404, '补充表附件不存在')
    digest = record.get('sha256', '')
    if (not re.fullmatch(r'[0-9a-f]{64}', str(digest)) or digest != source['revision']
            or record['filename'] != source['document_name']):
        raise HTTPException(409, '附件版本已变化，请重新查询')
    path = Path(directory) / 'supplement-files' / digest
    if not path.is_file() or not path.resolve().is_relative_to(Path(directory).resolve()):
        raise HTTPException(404, '补充表附件不存在')
    return path


def original_bytes(directory, record):
    digest = str(record.get('sha256', ''))
    if not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise TableError('附件版本无效')
    try:
        path = Path(directory) / 'supplement-files' / digest
        if not path.resolve().is_relative_to(Path(directory).resolve()):
            raise TableError('附件不在当前论文中')
        with path.open('rb') as stream:
            content = stream.read(MAX_BYTES + 1)
    except OSError as exc:
        raise TableError('附件文件已缺失') from exc
    if len(content) > MAX_BYTES:
        raise TableError('附件超过 20 MB 的单表查询上限')
    if hashlib.sha256(content).hexdigest() != digest:
        raise TableError('附件内容已变化，请重新添加附件')
    return content


def table_scope(meta, question):
    tables = [r for r in meta.get('supplement_files', []) if Path(r['filename']).suffix.lower() in EXTENSIONS]
    full_names = [r for r in tables if mentioned(norm(r['filename']), question)]
    if re.search(r'\.(?:xlsx|csv|tsv)\b', question, re.ASCII):
        return full_names
    refs = set(TABLE_REF.findall(question))
    if refs:
        return [r for r in tables if refs.intersection(TABLE_REF.findall(norm(Path(r['filename']).stem)))]
    named = [r for r in tables if mentioned(norm(Path(r['filename']).stem), question)]
    return named or tables


# A named worksheet is a hard restriction, including a name absent from all files.
SHEET_SELECTOR = re.compile(
    r"""(?:工作表|(?<![a-z0-9_])(?:work)?sheet(?![a-z0-9_]))(?:名为|名称为|为)?\s*[:=]?\s*
        (?:[`"'“‘「]([^`"'”’」]+)[`"'”’」]|([a-z0-9_][a-z0-9_.-]*)|
           (?![中里内上])[ ]*([㐀-鿿]+?)(?=[中里内上\s,，。]))""", re.X)
SHEET_BEFORE = re.compile(r"""(?:[`"'“‘「]([^`"'”’」]+)[`"'”’」]|([a-z0-9_.-]+))\s*工作表""")


def sheet_selection(question):
    selected = SHEET_SELECTOR.search(question) or SHEET_BEFORE.search(question)
    if selected:
        name = next(part for part in selected.groups() if part is not None)
        return name, question[:selected.start()] + ' ' + question[selected.end():]
    return '', question


def section_header(cells, question, metrics, previous_labels):
    # In stacked tables a repeated header can reorder or omit measurements.
    # A data row with a number can never introduce a new set of columns.
    populated = [value.strip() for value in cells if value.strip()]
    if len(populated) < 2 or any(NUMBER.fullmatch(value) for value in populated):
        return None
    labels = [norm(value) for value in cells]
    identifiers = [i for i, label in enumerate(labels) if re.sub(r'[\s._-]', '', label) in IDENTIFIERS]
    columns = [i for i, label in enumerate(labels)
               if i not in identifiers and (not metrics or label in metrics) and mentioned(label, question)]
    if not (identifiers or columns or previous_labels.intersection(label for label in labels if label)):
        return None
    if not identifiers:
        # Without a known identifier header, use the leading label column.
        identifiers = [i for i, label in enumerate(labels) if label and i not in columns][:1]
    return identifiers, columns


# Only metadata columns qualify observations. Statistical columns are never used
# as implicit row selectors. Explicit column=value also supports custom headers.
CONDITION_NAMES = {
    'time': ('time', 'timepoint', 'time_point', 'time point', '时间', '时间点'),
    'group': ('group', '分组', '组别', '组'),
    'condition': ('condition', 'treatment', '处理', '条件', '处理条件'),
    'contrast': ('contrast', 'comparison', '对比', '对照方向', '比较'),
    'tissue': ('tissue', 'tissue type', '组织', '组织类型'),
    'cell_type': ('cell type', 'cell_type', 'celltype', '细胞类型', '细胞群'),
    'cluster': ('cluster', '细胞簇', '簇'),
    'cohort': ('cohort', '队列'),
    'dose': ('dose', 'dosage', '剂量'),
}
CONDITION_ALIASES = {alias: key for key, names in CONDITION_NAMES.items() for alias in names}
TIME_UNIT = r'hours?|hrs?|h|days?|d|minutes?|mins?|min|weeks?|w|小时|天|分钟|周'
TIME_TOKEN = re.compile(r'(?<![\w.])(\d+(?:\.\d+)?)\s*(' + TIME_UNIT + r')(?!\w)', re.ASCII)
TIME_UNITS = {v: u for u, vals in {'h': ('h', 'hr', 'hrs', 'hour', 'hours', '小时'),
              'd': ('d', 'day', 'days', '天'), 'min': ('min', 'mins', 'minute', 'minutes', '分钟'),
              'w': ('w', 'week', 'weeks', '周')}.items() for v in vals}
FILTER = re.compile(
    r"""(?<![a-z0-9_])(cell[ -]type|time point|[a-z][a-z0-9_.-]*|时间点?|分组|组别|条件|处理|组织|细胞类型|对比|队列|剂量)\s*(?:[=:]|为)\s*"""
    r"""(?:["'“「]([^"'”」]+)["'”」]|(\d+(?:\.\d+)?\s*(?:""" + TIME_UNIT + r""")|"""
    r"""[a-z0-9_.+-]+(?:[ \t]+(?!(?:and|at|in|for|with|is|what)\b|[a-z0-9_.-]+\s*[:=为])[a-z0-9_.+-]+){0,7}"""
    r"""|[㐀-鿿]+?)(?=[时中里内的，。、,\s?？]|$))""", re.ASCII)
GROUP_PHRASE = re.compile(r'\b(?:in|for|from) (?:the )?([a-z0-9_-]+) group\b|'
                          r'\b(?:in|for|from) group ([a-z0-9_-]+)\b|'
                          r'(?:在|的|中|里|^|\s)((?:(?!在|的|中|里)[㐀-鿿]){1,8}?|[a-z][a-z0-9_-]*)\s*组(?=[的中里内，。、\s?？]|$)', re.ASCII)
CELL_PHRASE = re.compile(r'(?<![a-z0-9_])(?:(?:naive|memory|activated|exhausted|regulatory|cd4\+?|cd8\+?)'
                         r'[ -]+)?[a-z][a-z0-9+-]*[ -]*(?:cells?\b|细胞)', re.ASCII)
NAMED_SUBSET = re.compile(r'在\s*([a-z0-9][a-z0-9_+.-]*(?:[ \t]+[a-z0-9_+.-]+){0,5})\s*[中里内]', re.ASCII)
TIME_HEADER = re.compile(r'(time(?:[ _]?point)?|时间点?)\s*[(_\[]\s*(' + TIME_UNIT + r')\s*[)\]]?')
MAX_CHOICES = 8


def condition_value(key, text):
    value = norm(text)
    time = TIME_TOKEN.fullmatch(value) if key == 'time' else None
    if key == 'cell_type':
        return re.sub(r'[ -]*(?:cells?|细胞)$', ' cell', value).strip()
    return time[1] + TIME_UNITS[time[2]] if time else value


def condition_query(question):
    required = set()
    for token in FILTER.finditer(question):
        key = CONDITION_ALIASES.get(token[1], token[1])
        value = token[2] or token[3]
        if value not in ('多少', '什么', 'what') and not METRIC.fullmatch(key):
            required.add((key, condition_value(key, value)))
    for token in TIME_TOKEN.finditer(question):
        required.add(('time', condition_value('time', token[0])))
    bare = FILTER.sub(' ', question)
    for token in CELL_PHRASE.finditer(bare):
        required.add(('cell_type', condition_value('cell_type', token[0])))
    for token in NAMED_SUBSET.finditer(bare):
        value = token[1]
        if not TIME_TOKEN.fullmatch(value) and not CELL_PHRASE.fullmatch(value):
            required.add(('named_subset', norm(value)))
    for token in GROUP_PHRASE.finditer(bare):
        value = next(g for g in token.groups() if g)
        required.add(('group_phrase', norm(value)))
    return required


class TableMatches:
    def __init__(self, required):
        self.required = set(required)
        self.buckets = {}

    def add(self, terms, source):
        key = frozenset(terms)
        if key not in self.buckets and len(self.buckets) >= 64:
            raise TableError('匹配条件组合过多，请指定文件、工作表或更精确的分组')
        bucket = self.buckets.setdefault(key, [])
        if len(bucket) <= MAX_CHOICES:
            bucket.append(source)

    def selected(self):
        return [s for terms, hits in self.buckets.items() if self.required <= terms for s in hits][:MAX_CHOICES + 1]


def answer_for_cell(source):
    shown = source.get('display_value', source['value']) or f"原表隐藏数值（底层值 {source['value']}）"
    context = '，'.join(item['name'] + '=' + item['value'] for item in source.get('conditions', []))
    key = source['identifier'] + ('（' + context + '）' if context else '')
    return f"{key} 的 {source['column_name']} 为 {shown}。[S1]"


def _scan_sheet(record, name, rows, question, budget, matches, explicit):
    metrics = set(METRIC.findall(question))
    previous_labels = set()
    identifiers, columns, metadata = [], [], []
    requested_keys = {key for key, value in explicit}
    bare_question = FILTER.sub(' ', question)
    for number, cells in checked_rows(rows, budget):
        layout = section_header(cells, bare_question, metrics, previous_labels)
        if layout is not None:
            identifiers, columns = layout
            header, header_row = cells, number
            previous_labels = {norm(v) for v in cells if v.strip()}
            key_question = FILTER.sub(' ', question)
            for i in columns:
                key_question = re.sub(term_pattern(norm(header[i])), ' ', key_question, flags=re.ASCII)
            metadata = []
            for i, label in enumerate(header):
                label_text = norm(label)
                time_header = TIME_HEADER.fullmatch(label_text)
                key = 'time' if time_header else CONDITION_ALIASES.get(label_text, label_text)
                if i not in columns and i not in identifiers and (key in CONDITION_NAMES or key in requested_keys):
                    metadata.append((i, key, time_header[2] if time_header else ''))
            continue
        if not columns:
            continue
        keys = [(i + 1, cells[i].strip()) for i in identifiers if i < len(cells)
                and mentioned(norm(cells[i]), key_question)]
        conditions, terms = [], set()
        for i, key, unit in metadata:
            if i >= len(cells) or not cells[i].strip():
                continue
            value = cells[i].strip()
            qualified = value + unit if unit and re.fullmatch(r'\d+(?:\.\d+)?', value) else value
            term = (key, condition_value(key, qualified))
            phrase = ('group_phrase', norm(value).removesuffix('组'))
            if key in ('group', 'condition', 'cohort') and phrase in explicit:
                terms.add(phrase)
            subset = ('named_subset', norm(value))
            if subset in explicit:
                terms.add(subset)
            if term in explicit:
                terms.add(term)
            # A group named elsewhere on this sheet is also binding on the
            # requested gene. Never choose its sole row from a different group.
            if len(value) > 1 and mentioned(norm(value), bare_question):
                matches.required.add(term)
                terms.add(term)
            if keys:
                if len(conditions) >= 16 or len(value) > 256 or len(header[i]) > 128:
                    raise TableError('原表分组信息过长，请缩小查询范围')
                conditions.append({'column': i + 1, 'name': header[i], 'value': value})
        if not keys:
            continue
        for i in columns:
            value = cells[i].strip() if i < len(cells) else ''
            source = None
            key_column, key = keys[0]
            if NUMBER.fullmatch(value) and len(value) <= 120 and len(header[i]) <= 256 and len(key) <= 256:
                source = {'id': 'S1', 'kind': 'table', 'document_id': record['id'], 'document_name': record['filename'],
                          'revision': record['sha256'], 'sheet': name, 'row': number, 'header_row': header_row,
                          'column': i + 1, 'column_name': header[i], 'identifier': key, 'identifier_column': key_column,
                          'value': value, 'label': f"{record['filename']} · {name} · 第 {number} 行"}
                shown = cells.display(i)
                if len(shown) > 1024:
                    raise TableError('单元格显示值过长，无法保存可核验的引用')
                if shown != value:
                    source['display_value'] = shown
                source['quote'] = f"{key} | {header[i]} = " + (shown or f'原表隐藏数值（底层值 {value}）')
                if conditions:
                    source['conditions'] = conditions
            matches.add(terms, source)


def lookup(directory, meta, question, *, candidates=None):
    q = norm(question)
    reference = TABLE_REF.search(q)
    electronic = re.search(r'补充表|附表|表格|spreadsheet|supplement(?:ary|al)?\s+table|\.(?:xlsx|csv|tsv)\b', q, re.ASCII)
    supplement = bool(electronic or (reference and reference[1].startswith('s')))
    quantitative = re.search(r'多少|数值|查[一询找下]?|值[是为]|\b(?:what|value|find|lookup|look up)\b', q, re.ASCII)
    if not (reference or supplement or (quantitative and METRIC.search(q) and meta.get('supplement_files'))):
        return None
    if not quantitative and not METRIC.search(q):
        return None
    tables = table_scope(meta, q)
    if not tables:
        # Ordinary numbered tables live in the article PDF unless there is a matching attachment.
        return ('当前论文没有找到问题指定的补充表。', []) if supplement else None
    if re.search(r'总结|概括|汇总|summari[sz]e|summary|如何计算|how (?:is|are).*calculated', q):
        return None
    if re.search(r'比较|对比|分别|求和|中位数|平均[值数]?|总和|共多少|多少个|最[高低大小]|'
                 r'\b(compare|comparison|average|median|highest|lowest|sum|how many|difference between)\b|'
                 r'\bmean\s+(?:of|across|over)\b', FILTER.sub(' ', q), re.ASCII):
        return '这次查询支持读取单个原始数值，请指定一个行标识和原表列名。', []
    if len(tables) > MAX_FILES:
        return '待查询的附件过多，请在问题里指定文件名。', []
    # Remove file and table references so a gene named like a filename cannot
    # accidentally match a question that names only a file and a measurement.
    for record in sorted(tables, key=lambda r: len(r['filename']), reverse=True):
        q = q.replace(norm(record['filename']), ' ')
    q = TABLE_REF.sub(' ', q)
    selected_sheet, q = sheet_selection(q)
    explicit = condition_query(q)
    hits = TableMatches(explicit)
    named_hits = TableMatches(explicit)
    saw_named_sheet = False
    budget = [MAX_CELLS]
    try:
        for record in tables:
            data = original_bytes(directory, record)
            ext = Path(record['filename']).suffix.lower()
            with open_sheets(data, ext) as sheets:
                for name, rows in sheets:
                    if selected_sheet and norm(name) != selected_sheet:
                        continue
                    named = bool(selected_sheet) or mentioned(norm(name), q)
                    saw_named_sheet |= named
                    terms = q.replace(norm(name), ' ') if named else q
                    group = named_hits if named else hits
                    _scan_sheet(record, name, rows, terms, budget, group, explicit)
    except TableError as exc:
        return '无法核实补充表中的唯一数值：' + str(exc) + '。', []
    if selected_sheet and not saw_named_sheet:
        return '当前论文的指定附件中没有找到该工作表，请核对工作表名。', []
    group = named_hits if saw_named_sheet else hits
    hits = group.selected()
    if len(hits) >= 2:
        if candidates is not None:
            candidates.extend(s for s in hits[:MAX_CHOICES] if s is not None)
        if candidates:
            more = '仅显示前 8 条匹配；可补充分组条件进一步缩小范围。' if len(hits) > MAX_CHOICES else ''
            return '有多个匹配的行或列。请选择要引用的单元格。' + more, []
        return '有多个匹配的行或列，请指定分组、文件或工作表。', []
    if not hits:
        return '当前论文的补充表中未找到同时匹配行标识、列名和指定条件的数值。', []
    if hits[0] is None:
        return '找到了对应的行和列，但单元格为空、没有已保存的计算值，或不包含可直接引用的单一数值。', []
    return answer_for_cell(hits[0]), hits


def verified_table_source(directory, meta, source):
    table_source_path(directory, meta, source)
    record = {'sha256': source['revision'], 'filename': source['document_name']}
    target, column, header_row = source['row'], source['column'], source['header_row']
    left, right = max(1, column - 5), min(MAX_COLS, column + 5)
    conditions = source.get('conditions', [])
    columns = sorted(set(range(left, right + 1)) | {source['identifier_column']} | {c['column'] for c in conditions})
    preview = []
    verified = set()
    try:
        data = original_bytes(directory, record)
        with open_sheets(data, Path(record['filename']).suffix.lower()) as sheets:
            for name, rows in sheets:
                if name != source['sheet']:
                    continue
                for number, cells in checked_rows(rows, [MAX_CELLS]):
                    if number > target + 2:
                        break
                    if number == header_row or abs(number - target) <= 2:
                        preview.append({'number': number, 'cells': [cells.display(col - 1)[:1024] for col in columns if col <= len(cells)]})
                    if number in (header_row, target) and len(cells) >= column:
                        expected = source['value'] if number == target else source['column_name']
                        actual = cells[column-1].strip() if number == target else cells[column-1]
                        key_index = source['identifier_column'] - 1
                        if number == target and (key_index >= len(cells) or cells[key_index].strip() != source['identifier']):
                            continue
                        if number == target and 'display_value' in source and cells.display(column - 1) != source['display_value']:
                            continue
                        matches = all(c['column'] <= len(cells) and
                                      (cells[c['column'] - 1].strip() == c['value'] if number == target
                                       else cells[c['column'] - 1] == c['name']) for c in conditions)
                        if matches and expected == actual:
                            verified.add(number)
                break
    except TableError as exc:
        raise HTTPException(409, str(exc)) from exc
    if verified != {header_row, target}:
        raise HTTPException(409, '引用的工作表、列名或数值无法与原附件匹配，请重新查询')
    return {**source, 'first_column': columns[0], 'column_numbers': columns, 'rows': preview}
