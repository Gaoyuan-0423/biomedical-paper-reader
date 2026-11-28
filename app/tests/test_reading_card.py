"""Paper card excerpts must be attributable to saved material."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from reading_card import reading_card


class ReadingCardTests(unittest.TestCase):
    def card(self, notes='', **values):
        return reading_card(None, {'notes': notes, **values})

    def test_fielded_deep_note_and_exact_bound_reference(self):
        snapshot = '8' * 32
        notes = ('# 笔记中使用的标题\n\n**一句话发现：** 处理后增加，\n'
                 f'但次要指标没有改善。[N2](note-source:{snapshot}:N2)\n'
                 '**阅读建议：** 仅用于背景。\n\n## 文章信息\n'
                 '| 项目 | 内容 |\n|---|---|\n| 研究材料与设计 | 共 12 个样本。 |\n\n'
                 '## 研究概述\n**未解问题：** 不同时间点是否一致？\n\n'
                 '## 逐图精读\n### Figure 2｜两种时间点\n**方法与目的：** 比较两个时间点。\n'
                 '**实验设计与关键结果：** 差值为 −1.42；样本量小。\n'
                 '**证据边界：** 仅覆盖一组。\n\n'
                 '## 研究总结与课题借鉴\n- **核心模型与贡献：** 仅提出模型。\n'
                 '- **主要局限：** 随访短。\n- **值得迁移的研究设计：** 可沿用分组。\n'
                 '- **延伸方向（本次提出，尚未验证）：** 需要独立复核。')
        card = self.card(notes, notes_mode='deep', notes_source_snapshot=snapshot, notes_revision=4)
        self.assertEqual(card['note_title'], '笔记中使用的标题')
        self.assertEqual(card['conclusion'], '处理后增加， 但次要指标没有改善。')
        self.assertEqual(card['conclusion_source'], 'notes')
        self.assertEqual(card['conclusion_citation'], {'source_id': 'N2', 'snapshot_id': snapshot, 'notes_revision': 4})
        fields = {s['key']: s['text'] for s in card['sections']}
        self.assertEqual(len(fields), 8)
        for key, phrase in {'background':'不同时间点', 'design':'12 个样本', 'methods':'两个时间点',
                            'results':'−1.42', 'mechanism':'仅提出模型', 'limits':'随访短',
                            'implications':'可沿用分组', 'next':'独立复核'}.items():
            self.assertIn(phrase, fields[key])
        self.assertLess(fields['limits'].index('随访短'), fields['limits'].index('仅覆盖一组'))
        self.assertIn('Figure 2', fields['results'])
        self.assertNotIn('note-source:', str(card))

    def test_no_inferred_conclusion_from_metadata_or_custom_free_text(self):
        blank = self.card(abstract='We observed ABC.')
        self.assertFalse(blank['has_notes'])
        self.assertEqual(blank['abstract_excerpt'], 'We observed ABC.')
        self.assertFalse(blank['conclusion'])
        self.assertTrue(all(not s['text'] for s in blank['sections']))
        custom = self.card('# 自己记的\n\n要复查[这篇文章](https://example.org/)的分组。')
        self.assertTrue(custom['has_notes'])
        self.assertEqual(custom['mode'], 'notes')
        self.assertEqual(custom['note_excerpt'], '要复查这篇文章的分组。')
        self.assertFalse(custom['conclusion'])

    def test_quick_note_is_explicitly_provisional_and_has_coexisting_statuses(self):
        card = self.card('# 筛选\n\n**核心问题与暂定发现：** 两组可能有差异。\n\n'
                         '## 关键局限\n未覆盖远期。\n\n## 下一步优先回看\n表 2。',
                         notes_mode='quick', notes_incomplete=True, notes_material_revision=1, material_revision=2)
        self.assertEqual(card['conclusion_source'], 'quick_notes')
        self.assertTrue(card['incomplete'] and card['stale'])
        self.assertEqual(self.card('**暂定发现：** 仍待复核。')['conclusion_source'], 'quick_notes')
        fields = {s['key']: s['text'] for s in card['sections']}
        self.assertIn('未覆盖远期', fields['limits'])
        self.assertIn('表 2', fields['next'])

    def test_ignore_examples_private_comments_and_local_figure_conclusion(self):
        note = ('# Example\n\n> **核心结论：** quoted example\n\n'
                '~~~text\n## Conclusion\ncode example\n~~~\n\n'
                '<!-- **一句话发现：** internal -->\n\n## 逐图精读\n### Figure 2\n'
                '**结论：** 只支持这一图。\n\n## 关键局限\n结果受限。')
        card = self.card(note)
        self.assertFalse(card['conclusion'])
        self.assertFalse(self.card('## Figure 2\n**结论：** 仅针对这一图。')['conclusion'])
        self.assertNotIn('internal', str(card))
        self.assertNotIn('code example', str(card))
        self.assertNotIn('quoted example', str(card))

    def test_incorrect_or_stale_markers_cannot_supply_an_original_link(self):
        digest = 'a' * 32
        for ref in (f'[N9](note-source:{digest}:N2)', f'`[N2](note-source:{digest}:N2)`',
                    f'![N2](note-source:{digest}:N2)', '[N2](https://example.org)'):
            card = self.card('## Conclusion\nFinding. ' + ref, notes_source_snapshot=digest)
            self.assertIsNone(card['conclusion_citation'])
        stale = self.card(f'## Conclusion\nFinding. [N2](note-source:{digest}:N2)', notes_source_snapshot='f' * 32)
        self.assertIsNone(stale['conclusion_citation'])

    def test_heading_aliases_unlabelled_text_roundtrip_and_bounded_fields(self):
        card = self.card('# T\n\n## 2. Methods\n**Bold material** and 0.125.\n\n'
                         '## Results\n' + ('Unchanged, ' * 110) + '\n\n## Discussion\nCaution.')
        fields = {s['key']: s['text'] for s in card['sections']}
        self.assertIn('Bold material and 0.125.', fields['methods'])
        self.assertTrue(fields['results'].endswith('…'))
        self.assertNotIn('Caution', fields['results'])
        self.assertFalse(card['conclusion'])
