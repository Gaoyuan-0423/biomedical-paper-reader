"""Filtered resource exports preserve paper usage and remain safe in spreadsheets."""
import copy
import csv
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from resource_catalog import accession_text, filter_resources, resource_csv


class ResourceCatalogTests(unittest.TestCase):
    def record(self, **changes):
        return dict(paper_id='1' * 32, paper_title='Synthetic stomach study', paper_doi='10.1234/synthetic',
                    id='2' * 32, title='GEO 联合队列', kind='dataset', role='reused',
                    identifiers='GSE11111；GSE22222', accession='', url='', repository='GEO',
                    purpose='外部验证', organism='人；小鼠', tissue='胃组织', disease='胃癌', modality='scRNA-seq',
                    platform='10x Chromium', sample_groups='肿瘤、正常', sample_count='肿瘤 8 例；正常 5 例',
                    sample_details='按供者分析；不能等同数据库总量', conditions='仅使用已处理矩阵',
                    notes='人工说明', document_id='main', page=3, source_name='main.pdf',
                    quote='Thirteen donors were included.', source_available=True,
                    **changes)

    def test_filter_and_query_use_normalization_and_keep_cross_paper_links(self):
        first = self.record()
        first['related_papers'] = [{'id': '3' * 32, 'title': 'Another paper'}]
        second = {**first, 'id': '4' * 32, 'tissue': '血液', 'disease': '白血病'}
        before = copy.deepcopy([first, second])
        result = filter_resources([first, second], q='ＳＹＮＴＨＥＴＩＣ', organism='小鼠',
                                  tissue='胃', disease='胃癌', modality='ＳＣＲＮＡ－ＳＥＱ', platform='10X')
        self.assertEqual(result, [first])
        self.assertEqual(result[0]['related_papers'], first['related_papers'])
        self.assertEqual(filter_resources([first], q='THIRTEEN   DONORS'), [first])
        self.assertEqual(filter_resources([first], q='10.1234/synthetic'), [first])
        self.assertEqual(filter_resources([first], q='正常 5 例'), [first])
        self.assertEqual(filter_resources([first], platform='BD Rhapsody'), [])
        self.assertEqual(filter_resources([first], tissue='血液', disease='胃癌'), [])
        self.assertEqual([first, second], before)

    def test_csv_roundtrip_grouped_rows_unicode_multiline_and_no_geo_or_status(self):
        first = self.record()
        first.update(notes='含逗号,和"引号"\n多行备注', status='verified',
                     geo_metadata={'geo_sample_count': 99999}, geo_sample_count=99999)
        before = copy.deepcopy(first)
        raw = resource_csv([first])
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
        self.assertIn(b'\r\n', raw)
        rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline='')))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['编号／链接'], 'GSE11111；GSE22222')
        self.assertEqual(row['论文实际样本量'], '肿瘤 8 例；正常 5 例')
        self.assertEqual(row['备注'], first['notes'])
        self.assertEqual(row['原文文件'], 'main.pdf')
        self.assertEqual(row['PDF 页码'], '3')
        self.assertEqual(row['论文 DOI'], '10.1234/synthetic')
        self.assertEqual(row['原文可用情况'], '可定位原文')
        self.assertNotIn('99999', raw.decode('utf-8-sig'))
        self.assertNotIn('核对', raw.decode('utf-8-sig'))
        self.assertEqual(first, before)

    def test_csv_formula_prefixes_are_literal_in_every_exported_field(self):
        attacks = ['=SUM(1,2)', '+1+2', '-1+2', '@SUM(1)', '  =HYPERLINK("https://example.org")',
                   '\t=1', '\r=1', '\n=1', '\x00=1', '\ufeff=1', '＝1+1']
        for attack in attacks:
            with self.subTest(attack=attack):
                record = self.record()
                record.update(title=attack, paper_title=attack, purpose=attack, sample_count=attack, quote=attack)
                rows = list(csv.DictReader(io.StringIO(resource_csv([record]).decode('utf-8-sig'), newline='')))
                for field in ('资源名称', '论文标题', '用途', '论文实际样本量', '原文摘录'):
                    self.assertTrue(rows[0][field].startswith("'"))

    def test_csv_sources_and_legacy_snippets_are_explicit_without_review_workflow(self):
        base = self.record()
        records = [dict(base, notes='来自精读笔记自动整理，尚未核对原文。笔记片段：\n| OLD-SNIPPET |',
                        source_available=False),
                   dict(base, source_changed=True), dict(base, document_id='', page=None, source_name='')]
        rows = list(csv.DictReader(io.StringIO(resource_csv(records).decode('utf-8-sig'), newline='')))
        self.assertEqual(rows[0]['备注'], '')
        self.assertEqual(rows[0]['整理来源'], '精读笔记自动整理')
        self.assertEqual([r['原文可用情况'] for r in rows],
                         ['来源已移除或页码失效', '来源文件已更新', '未关联原文'])
        self.assertEqual(rows[2]['PDF 页码'], '')

    def test_accessions_only_from_explicit_identifier_fields_and_official_links(self):
        records = [dict(self.record(), notes='Do not extract SRR99999', purpose='GSE88888 is a proposal'),
                   dict(self.record(), identifiers='gse11111、OMIX001073', accession='SRR100',
                        url='https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE33333'),
                   dict(self.record(), identifiers='https://example.org/GSE55555；GSE100-200；TCGA-STAD',
                        accession='', url='')]
        self.assertEqual(accession_text(records).decode().splitlines(),
                         ['GSE11111', 'GSE22222', 'GSE33333', 'OMIX001073', 'SRR100'])
        self.assertEqual(accession_text([]), b'')
        self.assertEqual(len(list(csv.reader(io.StringIO(resource_csv([]).decode('utf-8-sig'))))), 1)


if __name__ == '__main__':
    unittest.main()
