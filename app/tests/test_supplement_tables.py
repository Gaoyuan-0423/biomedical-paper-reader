import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from openpyxl import Workbook
from test_documents import TestClient, fixture_pdf, main
import supplement_tables as tables


def workbook(sheets):
    book = Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    buf = io.BytesIO()
    book.save(buf)
    book.close()
    return buf.getvalue()


class SupplementTableTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.directory = Path(folder)
        self.meta = {'supplement_files': []}

    def attach(self, name, data):
        digest = hashlib.sha256(data).hexdigest()
        folder = self.directory / 'supplement-files'
        folder.mkdir(exist_ok=True)
        (folder / digest).write_bytes(data)
        self.meta['supplement_files'].append({'id': digest, 'filename': name, 'sha256': digest})

    def lookup(self, question):
        return tables.lookup(self.directory, self.meta, question)

    def test_excel_formats_retain_raw_values_through_lookup_and_preview(self):
        book = Workbook()
        sheet = book.active
        sheet.append(['gene', 'rate'])
        examples = [('PCT', 0.125, '0.0%', '12.5%'), ('FRAC', 0.125, 'General', '0.125'),
                    ('SCI', 0.000001, '0.00E+00', '1.00E-06'),
                    ('UNIT', 2, '0.00" mg"', '2.00 mg'), ('HIDDEN', 3, ';;;', '')]
        for gene, value, style, _ in examples:
            sheet.append([gene, value])
            sheet.cell(sheet.max_row, 2).number_format = style
        data = io.BytesIO()
        book.save(data)
        book.close()
        self.attach('Table S2.xlsx', data.getvalue())
        for gene, value, _, display in examples:
            with self.subTest(gene=gene):
                text, (source,) = self.lookup(f'Table S2 中 {gene} 的 rate 是多少？')
                self.assertEqual(source['value'], str(value))
                self.assertEqual(source.get('display_value', source['value']), display)
                self.assertIn(display or '隐藏', text)
                preview = tables.verified_table_source(self.directory, self.meta, source)
                row = next(r for r in preview['rows'] if r['number'] == source['row'])
                self.assertEqual(row['cells'][1], display)
                with self.assertRaises(HTTPException):
                    tables.verified_table_source(self.directory, self.meta, dict(source, display_value='other'))

    def test_generic_numeric_column_and_csv_record_coordinates(self):
        self.attach('Counts.csv', b'\xef\xbb\xbfSupplementary data\r\n\r\nSample,Count,Description\r\nA11,4,"multi\nline"\r\nA1,008,case\r\n')
        text, (source,) = self.lookup('Counts.csv 中 A1 的 Count 是多少？')
        self.assertIn('008', text)
        self.assertEqual((source['sheet'], source['row'], source['column'], source['value']), ('CSV', 5, 2, '008'))
        preview = tables.verified_table_source(self.directory, self.meta, source)
        self.assertEqual(preview['rows'][-1], {'number': 5, 'cells': ['A1', '008', 'case']})
        self.assertEqual(preview['rows'][-2]['cells'][2], 'multi\nline')

    def test_excel_empty_preface_formula_and_duplicate_column_or_row(self):
        self.attach('Table S2.xlsx', workbook({
            'DEG': [[], ['Gene', 'log2FC', 'FDR'], ['TP530', 0, 0.1], [], ['TP53', -1.75, 0.02],
                    ['NOCACHE', '=2+3', 0.4], ['DUP', 5, 0.6], ['DUP', 4, 0.7]],
            'Repeat': [['Gene', 'log2FC', 'log2FC'], ['TP53', 12, 21]]
        }))
        _, (s,) = self.lookup('Table S2.xlsx 工作表 DEG 中 TP53 的 log2FC 是多少')
        self.assertEqual((s['row'], s['header_row'], s['value'], s['identifier_column']), (5, 2, '-1.75', 1))
        for question in ('工作表 Missing 中 TP53 的 log2FC 是多少', '工作表 DEG 中 TP 的 log2FC 是多少',
                         '工作表 DEG 中 TP53 的 P_value 是多少', '工作表 DEG 里 NOCACHE 的 log2FC 是多少',
                         '工作表 DEG 里 DUP 的 log2FC 是多少', '工作表 Repeat 中 TP53 的 log2FC 是多少',
                         'TP53 的 log2FC 是多少'):
            with self.subTest(question=question):
                self.assertEqual(self.lookup('Table S2.xlsx 的' + question)[1], [])
        for changes in ({'value': '2'}, {'row': 3}, {'identifier': 'OTHER'}, {'column_name': 'Count'}):
            with self.subTest(changes=changes), self.assertRaises(HTTPException):
                tables.verified_table_source(self.directory, self.meta, {**s, **changes})

    def test_missing_exact_file_or_sheet_cannot_fall_back_to_other_file(self):
        self.attach('Table S20.tsv', b'Gene\tlog2FC\nTP53\t99\n')
        self.assertFalse(self.lookup('查 Table S2 中 TP53 的 log2FC')[1])
        self.assertFalse(self.lookup('查 Table S20.xlsx 中 TP53 的 log2FC')[1])
        self.assertEqual(self.lookup('查 Table S20 中 TP53 的 log2FC')[1][0]['value'], '99')
        self.attach('Table S2.xlsx', workbook({'Chosen': [['Gene', 'log2FC'], ['TP53', 1]]}))
        self.attach('Table S2.xlsx', workbook({'Other': [['Gene', 'log2FC'], ['TP53', 2]]}))
        self.assertEqual(self.lookup('Table S2.xlsx 工作表 Chosen 里 TP53 的 log2FC 是多少')[1][0]['value'], '1')
        self.assertFalse(self.lookup('查 Table S2.xlsx 中 TP53 的 log2FC')[1])
        self.assertIsNone(self.lookup('请解释作者是如何计算 log2FC 的'))
        self.assertIsNone(self.lookup('这篇论文有多少张图？'))

    def test_qualified_rows_and_pinned_identifier_in_wide_preview(self):
        headers = ['gene', 'timepoint', 'cell_type'] + [f'attr{i}' for i in range(12)] + ['log2FC']
        rows = [headers] + [[symbol, time, cell] + [''] * 12 + [value] for symbol, time, cell, value in (
            ('FLC', '6h', 'T cell', 1.42), ('FLC', '24h', 'T cell', -0.73), ('FLC', '24h', 'B cell', 0.18))]
        self.attach('Table S2.xlsx', workbook({'DEGs': rows}))
        candidates = []
        _, sources = tables.lookup(self.directory, self.meta, 'FLC 的 log2FC 是多少', candidates=candidates)
        self.assertEqual(sources, [])
        self.assertEqual([s['row'] for s in candidates], [2, 3, 4])
        for query in ('cell_type 为 T cell 时，24 h 的 FLC log2FC 是多少', 'FLC 在 T 细胞 24小时的 log2FC 是多少',
                      'What is the FLC log2FC with cell type="T cell" at 24 hours?'):
            with self.subTest(query=query):
                _, (source,) = self.lookup(query)
                self.assertEqual((source['column'], source['row'], source['value']), (16, 3, '-0.73'))
        preview = tables.verified_table_source(self.directory, self.meta, source)
        self.assertEqual(preview['column_numbers'][:3], [1, 2, 3])
        target = next(r for r in preview['rows'] if r['number'] == 3)
        self.assertEqual(target['cells'][:3], ['FLC', '24h', 'T cell'])
        self.assertEqual(target['cells'][preview['column_numbers'].index(16)], '-0.73')
        invalid = {**source, 'conditions': [{**source['conditions'][0], 'value': '6h'}]}
        with self.assertRaises(HTTPException):
            tables.verified_table_source(self.directory, self.meta, invalid)

    def test_unknown_group_does_not_fall_back_to_another_genes_only_row(self):
        self.attach('Table S2.csv', b'gene,cell_type,group,time (h),log2FC\nFLC,T cell,treated,6,1.42\nABC1,B cell,control,24,-0.73\n')
        for request in ('NK cell 中 FLC 的 log2FC 是多少', 'FLC 在 NK 细胞的 log2FC 是多少',
                        'FLC 在 Treg 中的 log2FC 是多少', 'FLC 在 UnknownGroup 组的 log2FC 是多少',
                        'B cell 中 FLC 的 log2FC 是多少', 'FLC 24h 的 log2FC 是多少',
                        'What is FLC log2FC for the missing group?', 'group=vehicle 时 FLC 的 log2FC 是多少',
                        'control 中 FLC 的 log2FC 是多少'):
            with self.subTest(request=request):
                choices = []
                answer, sources = tables.lookup(self.directory, self.meta, request, candidates=choices)
                self.assertIn('未找到', answer)
                self.assertEqual((sources, choices), ([], []))
        for query in ('cell_type 为 T cell 时，FLC 的 log2FC 是多少', '细胞类型为 T cell 时 FLC 的 log2FC 是多少',
                      'FLC 的 log2FC 在 6 小时为多少', 'FLC 在 treated 中的 log2FC 是多少',
                      'What is the FLC log2FC for the treated group?'):
            with self.subTest(query=query):
                self.assertEqual(self.lookup(query)[1][0]['value'], '1.42')

    def test_late_filtered_hit_and_bounded_duplicate_choices(self):
        self.attach('Table S2.csv', ('gene,timepoint,log2FC\n' + ''.join(
            f'FLC,{i}h,{i}\n' for i in range(1, 15))).encode())
        choices = []
        answer, sources = tables.lookup(self.directory, self.meta, 'FLC 的 log2FC 是多少', candidates=choices)
        self.assertEqual((len(choices), sources), (8, []))
        self.assertIn('前 8 条', answer)
        self.assertEqual(self.lookup('FLC 14h 的 log2FC 是多少')[1][0]['row'], 15)

    def test_stacked_tables_reorder_or_remove_target_column(self):
        self.attach('Table S2.tsv', b'Gene\tlog2FC\tpadj\nA\t1.1\t0.03\n\n'
                                    b'Gene\tpadj\tlog2FC\nFLC\t0.02\t-7.25\n\n'
                                    b'Gene\tpadj\tcount\nB\t0.1\t12\n')
        _, (source,) = self.lookup('Table S2 中 FLC 的 log2FC 是多少')
        self.assertEqual((source['value'], source['row'], source['column'], source['header_row']), ('-7.25', 5, 3, 4))
        self.assertEqual(tables.verified_table_source(self.directory, self.meta, source)['header_row'], 4)
        self.assertFalse(self.lookup('Table S2 中 B 的 log2FC 是多少')[1])

    def test_pdf_table_queries_and_aggregate_intent(self):
        for question in ('表 2 中 TP53 的 log2FC 是多少？', 'What is the sample size in Table 2?'):
            self.assertIsNone(self.lookup(question))
        self.attach('Table S2.tsv', b'Gene\tlog2FC\nTP53\t1.25\n')
        self.assertIsNone(self.lookup('表 2 中 TP53 的 log2FC 是多少？'))
        for question in ('Table S2 里 TP53 的 log2FC 平均值是多少？',
                         'Compare the log2FC of TP53 and TP54 in Table S2',
                         'What is the highest log2FC in Table S2?'):
            answer, sources = self.lookup(question)
            self.assertFalse(sources)
            self.assertNotIn('1.25', answer)

    def test_changed_table_or_search_limit_does_not_report_unique_hit(self):
        data = b'Gene\tlog2FC\nA\t1\nB\t2\nA\t1\n'
        self.attach('S2.tsv', data)
        with patch.object(tables, 'MAX_ROWS', 3):
            text, refs = self.lookup('查 S2.tsv 中 A 的 log2FC')
            self.assertIn('超出', text)
            self.assertEqual(refs, [])
        _, (source,) = self.lookup('查 S2.tsv 中 B 的 log2FC')
        path = tables.table_source_path(self.directory, self.meta, source)
        path.write_bytes(b'Gene\tlog2FC\nB\t3\n')
        self.assertFalse(self.lookup('查 S2.tsv 中 B 的 log2FC')[1])
        with self.assertRaises(HTTPException):
            tables.verified_table_source(self.directory, self.meta, source)

    def test_manual_upload_and_existing_pmc_file_are_both_readable(self):
        self.enterContext(patch.object(main, 'PAPERS_DIR', self.directory / 'papers'))
        client = self.enterContext(TestClient(main.app))
        paper = client.post('/api/papers', files={'file': ('main.pdf', fixture_pdf())}).json()
        root = '/api/papers/' + paper['id']
        data = workbook({'Sheet 1': [['Gene', 'log2FC'], ['EGR1', 0.5]]})
        response = client.post(root + '/supplements', files={'file': ('Table S2.xlsx', data)})
        self.assertEqual(response.status_code, 200, response.text)
        record = response.json()['supplement_files'][0]
        self.assertEqual(client.get(root + '/supplement-files/' + record['id']).content, data)
        self.assertEqual(client.post(root + '/supplements', files={'file': ('Table S2.xlsx', data)}).status_code, 409)
        self.assertEqual(client.post(root + '/supplements', files={'file': ('bad.xlsx', b'bad')}).status_code, 422)
        self.assertEqual(len(client.get(root).json()['supplement_files']), 1)
        # PMC uses a 64-hex candidate id as well as its own provenance attributes.
        record.update(id='a' * 64, source_provider='PMC', candidate_id='a' * 64)
        _, (source,) = tables.lookup(main.paper_dir(paper['id']), {'supplement_files': [record]},
                                     'Table S2.xlsx 工作表「Sheet 1」中 EGR1 的 log2FC 是多少')
        self.assertEqual((source['value'], source['document_id']), ('0.5', 'a' * 64))
