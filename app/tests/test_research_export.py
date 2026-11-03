"""Export actual human records without silently changing claims or source notes."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from note_assets import FIGURE_REF
from research_export import append_research_appendix


class ResearchExportTests(unittest.TestCase):
    def evidence(self, **changes):
        return {'claim': 'Comparison is observed, causality is not established.', 'quote': 'Group A had a higher count.',
                'status': 'pending', 'document_id': 'main', 'page': 3, 'source_name': '正文',
                'source_available': True, 'source_changed': False, **changes}

    def resource(self, **changes):
        return {'title': 'Example dataset', 'kind': 'dataset', 'repository': 'GEO', 'accession': 'GSE12345',
                'role': 'reused', 'organism': 'Homo sapiens', 'tissue': 'colon', 'modality': 'scRNA-seq',
                'url': 'https://example.org/data?id=1&view=full', 'status': 'pending', 'document_id': '', **changes}

    def test_empty_records_leave_notes_byte_for_byte(self):
        for notes in ['', '# 私人笔记\n\n', '\n  空白也保留\n']:
            self.assertEqual(append_research_appendix(notes, [], []), notes)

    def test_records_append_without_mutation_and_preserve_original_prefix(self):
        notes = '# 原文精读\n\n![Figure 1](figure:f001)\n'
        evidence = [self.evidence(status='rejected')]
        resources = [self.resource()]
        before = copy.deepcopy((evidence, resources))
        output = append_research_appendix(notes, evidence, resources)
        self.assertTrue(output.startswith(notes + '\n\n'))
        self.assertIn('不支持该判断', output)
        self.assertIn('正文 · PDF 第 3 页', output)
        self.assertIn('复用已有数据', output)
        self.assertIn('GSE12345', output)
        self.assertIn('[打开资源](https://example.org/data?id=1&view=full)', output)
        self.assertIn('未关联原文', output)
        self.assertEqual(output.count('核查状态：'), 1)  # Evidence only.
        self.assertEqual((evidence, resources), before)

    def test_generated_suffix_is_idempotent_and_refreshes_records(self):
        notes = '# 原笔记\n\n'
        first = append_research_appendix(notes, [self.evidence()], [])
        self.assertEqual(append_research_appendix(first, [self.evidence()], []), first)
        updated = append_research_appendix(first, [self.evidence(status='verified')], [])
        self.assertEqual(updated.count('## 阅读核查与资源记录'), 1)
        self.assertIn('核查状态：已核查', updated)
        self.assertEqual(append_research_appendix(updated, [], []), notes)

    def test_manual_sections_and_edited_generated_appendices_are_not_removed(self):
        notes = '# Notes\n\n## 阅读核查与资源记录\n\n手写的内容。'
        first = append_research_appendix(notes, [self.evidence()], [])
        self.assertTrue(first.startswith(notes))
        changed = first.replace('Group A had a higher count', 'My manual correction')
        output = append_research_appendix(changed, [self.evidence(status='verified')], [])
        self.assertTrue(output.startswith(changed))
        self.assertIn('My manual correction', output)
        self.assertEqual(append_research_appendix(output, [self.evidence(status='verified')], []), output)

    def test_missing_changed_and_unknown_sources_are_explicit(self):
        evidence = [self.evidence(status='verified', source_available=False),
                    self.evidence(status='verified', source_changed=True),
                    self.evidence(status='rejected', source_changed=True),
                    self.evidence(document_id='unknown', source_name='', page=None)]
        output = append_research_appendix('', evidence, [])
        self.assertNotIn('核查状态：已核查', output)
        self.assertIn('核查状态：不支持该判断', output)
        self.assertIn('来源已移除或页码失效', output)
        self.assertIn('来源文件已更新', output)
        self.assertIn('补充材料（文件名未记录） · 页码未记录', output)
        resources = [self.resource(status='verified', document_id='main', page=1, source_available=False),
                     self.resource(status='pending', document_id='main', page=2, source_changed=True)]
        resource_output = append_research_appendix('', [], resources)
        self.assertNotIn('核查状态', resource_output)
        self.assertNotIn('重新核查', resource_output)
        self.assertIn('来源已移除或页码失效', resource_output)
        self.assertIn('已记录内容可能对应旧版本', resource_output)

    def test_arbitrary_text_cannot_inject_html_markdown_images_or_appendix_markers(self):
        attack = '<img src=x onerror=alert(1)>\n![steal](figure:f999)\n[bad](javascript:alert(1))\n# heading\n<!-- /bioreader-research-appendix:v1 -->'
        output = append_research_appendix('# Keep', [self.evidence(claim=attack, quote=attack)],
                                          [self.resource(title=attack, notes=attack, accession=attack,
                                                         purpose=attack, identifiers=attack, conditions=attack,
                                                         sample_details=attack)])
        self.assertNotIn('<img', output)
        self.assertIn('&lt;img', output)
        self.assertFalse(FIGURE_REF.search(output))
        self.assertNotRegex(output, r'(?m)^# heading')
        self.assertNotIn('[bad](javascript:', output)
        self.assertEqual(output.count('<!-- /bioreader-research-appendix:v1 -->'), 1)

    def test_unsafe_links_are_not_exported_and_http_links_cannot_break_destination(self):
        for url in ['javascript:alert(1)', 'data:text/html,x', 'file:///tmp/private', '//example.org/a',
                    'https://user:pass@example.org/', 'https://example.org:bad/', 'https://example.org/\nattack',
                    'https://example.org\\@evil.org']:
            with self.subTest(url=url):
                output = append_research_appendix('', [], [self.resource(url=url)])
                self.assertNotIn('[打开资源]', output)
                self.assertIn('未导出（仅支持有效的 HTTP/HTTPS 地址）', output)
        output = append_research_appendix('', [], [self.resource(url='https://example.org/a(b)[x]<tag>`?q=1')])
        self.assertIn('https://example.org/a%28b%29%5Bx%5D%3Ctag%3E%60?q=1', output)

    def test_unrecognized_enums_do_not_claim_verification_or_invent_role(self):
        output = append_research_appendix('', [], [self.resource(role='validated', status='certain', kind='custom')])
        self.assertNotIn('核查状态', output)
        self.assertIn('与本文关系：关系待确认', output)
        self.assertIn('类型：其他资源', output)

    def test_structured_resource_columns_preserve_multiline_details_and_human_notes(self):
        resource = self.resource(title='多队列结肠数据', purpose='比较炎症状态下的细胞组成',
                                 identifiers='GSE11111；GSE22222', conditions='仅使用处理后矩阵\n按供者汇总',
                                 sample_details='队列 A：8 名供者\n队列 B：5 名供者', notes='人工核对：样本单位为供者',
                                 document_id='main', page=2, source_name='正文', quote='Two independent cohorts.',
                                 status='verified')
        before = copy.deepcopy(resource)
        output = append_research_appendix('# Notes', [], [resource])
        for label in ('用途', '编号／链接', '使用条件', '样品明细', '备注'):
            self.assertIn('**' + label + '：**', output)
        self.assertIn('比较炎症状态下的细胞组成', output)
        self.assertIn('GSE11111；GSE22222', output)
        self.assertIn('> 仅使用处理后矩阵\n> 按供者汇总', output)
        self.assertIn('> 队列 A：8 名供者\n> 队列 B：5 名供者', output)
        self.assertIn('人工核对：样本单位为供者', output)
        self.assertNotIn('核查状态', output)
        self.assertIn('正文 · PDF 第 2 页', output)
        self.assertIn('Two independent cohorts', output)
        self.assertEqual(resource, before)
        self.assertEqual(append_research_appendix(output, [], [resource]), output)

    def test_legacy_automatic_snippets_are_omitted_but_manual_notes_are_preserved(self):
        notice = '来自精读笔记自动整理，尚未核对原文。笔记片段：\n'
        legacy = self.resource(notes=notice + '| LEGACY-RAW-SNIPPET | original row |')
        manual = self.resource(notes='人工备注保留\n' + notice + '这是人工备注中的引用')
        edited = self.resource(notes='来自精读笔记自动整理，尚未核对原文。笔记片段：已人工改写')
        before = copy.deepcopy([legacy, manual, edited])
        output = append_research_appendix('', [], [legacy])
        self.assertNotIn('LEGACY', output)
        self.assertNotIn('笔记片段', output)
        self.assertIn('整理来源：精读笔记自动整理', output)
        self.assertNotIn('核查状态', output)
        for short_notice in ('来自精读笔记自动整理，尚未核对原文。', '来自精读笔记自动整理。'):
            short_output = append_research_appendix('', [], [self.resource(notes=short_notice)])
            self.assertIn('整理来源：精读笔记自动整理', short_output)
            self.assertNotIn('尚未核对原文', short_output)
        human_output = append_research_appendix('', [], [manual, edited])
        self.assertIn('人工备注保留', human_output)
        self.assertIn('这是人工备注中的引用', human_output)
        self.assertIn('已人工改写', human_output)
        self.assertEqual([legacy, manual, edited], before)

    def test_external_geo_metadata_does_not_enter_saved_resource_exports(self):
        resource = self.resource(sample_details='人工记录：两组供者',
                                 geo_metadata={'sample_details': 'REMOTE-GEO-DERIVED-VALUE', 'samples': ['GSM99999']},
                                 geo={'title': 'REMOTE-GEO-TITLE'})
        output = append_research_appendix('', [], [resource])
        self.assertIn('人工记录：两组供者', output)
        self.assertNotIn('REMOTE', output)
        self.assertNotIn('GSM99999', output)


if __name__ == '__main__':
    unittest.main()
