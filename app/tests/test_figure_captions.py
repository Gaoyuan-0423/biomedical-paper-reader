"""Original synthetic PDFs and geometry: recover legends without changing figure crops."""
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from figures import (figure_regions, lines_from_words, parse_pdf, reflow_caption,
                     _caption_rows, _caption_can_continue, _continue_caption)


def line(text, top, size=10, left=60, right=500, bold=False):
    return {'text': text, 'bbox': [left, top, right, top + size], 'size': size, 'bold': bold}


def page(number=1, objects=True, **extra):
    result = SimpleNamespace(page_number=number, width=600, height=800, rotation=0,
        bbox=(0, 0, 600, 800), cropbox=(0, 0, 600, 800), mediabox=(0, 0, 600, 800),
        images=[{'x0': 60, 'top': 100, 'x1': 500, 'bottom': 450, 'width': 440, 'height': 350}] if objects else [], curves=[], rects=[])
    for key, value in extra.items():
        setattr(result, key, value)
    return result


def pdf_bytes(pages):
    """Each page contains authored text/rectangles only; no external paper assets."""
    writer = PdfWriter()
    for items in pages:
        target = writer.add_blank_page(width=600, height=800)
        fonts = {}
        for name, base in [('F1', '/Helvetica'), ('F2', '/Helvetica-Bold')]:
            font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject(base)})
            fonts[NameObject('/' + name)] = writer._add_object(font)
        target[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject(fonts)})
        commands = []
        for item in items:
            if item[0] == 'rect':
                _, left, top, right, bottom = item
                commands.append(f'0.2 0.4 0.6 rg {left} {800-bottom} {right-left} {bottom-top} re f')
            else:
                _, text, left, top, size, font = item
                escaped = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
                commands.append(f'0 0 0 rg BT /{font} {size} Tf {left} {800-top-size} Td ({escaped}) Tj ET')
        stream = DecodedStreamObject(); stream.set_data('\n'.join(commands).encode('ascii'))
        target[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO(); writer.write(output); return output.getvalue()


def text(value, top, left=60, size=10, bold=False):
    return ('text', value, left, top, size, 'F2' if bold else 'F1')


class FigureCaptionTests(unittest.TestCase):
    def parse(self, pages):
        with tempfile.TemporaryDirectory(prefix='bioreader-caption-test-') as temporary:
            directory = Path(temporary); source = directory / 'original-fixture.pdf'
            source.write_bytes(pdf_bytes(pages))
            return parse_pdf(source, directory / 'figures')

    def test_actual_pdf_label_to_smaller_body_and_bold_panel_font(self):
        parsed = self.parse([[('rect', 60, 100, 500, 450),
            text('Supplementary Figure 1', 500, size=10.8, bold=True),
            text('(A)', 520, size=9.504, bold=True),
            text('Original synthetic caption explains the treatment groups.', 520, left=80, size=9.504),
            text('(B) A second controlled panel reports the measured outcome.', 534, size=9.504),
            text('Methods', 548, size=12, bold=True),
            text('Unrelated methods must remain outside the legend.', 566)]])
        figure = parsed['figures'][0]
        self.assertEqual(figure['label'], 'Figure 1')
        self.assertEqual(figure['page'], 1)
        self.assertEqual(figure['bbox'], [52.0, 88.0, 508.0, 455.0])
        self.assertIn('(A) Original synthetic caption', figure['caption'])
        self.assertIn('(B) A second controlled panel', figure['caption'])
        self.assertNotIn('Methods', figure['caption'])
        self.assertNotIn('Unrelated', figure['caption'])

    def test_same_baseline_fragments_preserve_word_order(self):
        # Font changes with slightly different baselines previously put a panel
        # marker after the sentence or on a separate line.
        words = [
            {'text': 'Before', 'x0': 60, 'x1': 92, 'top': 520, 'bottom': 530, 'size': 10, 'fontname': 'Helvetica'},
            {'text': '(C)', 'x0': 95, 'x1': 109, 'top': 523.5, 'bottom': 533.5, 'size': 10, 'fontname': 'Helvetica-Bold'},
            {'text': 'after.', 'x0': 112, 'x1': 142, 'top': 520, 'bottom': 530, 'size': 10, 'fontname': 'Helvetica'},
        ]
        rows = _caption_rows(lines_from_words(words), (0, 600))
        self.assertEqual([row['text'] for row in rows], ['Before (C) after.'])

    def test_same_baseline_description_after_short_label_is_not_lost(self):
        lines = [line('Figure 1', 500, right=115), line('Inline description follows.', 500, left=145), line('A second line belongs to this legend.', 516)]
        result = figure_regions(page(), lines)[0]
        self.assertEqual(result[2], [52.0, 88.0, 508.0, 455.0])
        self.assertIn('Figure 1 Inline description follows.', result[1])
        self.assertIn('A second line', result[1])

    def test_title_body_font_transition_and_paragraph_spacing(self):
        for label in ['Supplementary Figure 1', 'Supplementary Fig. S1', 'Supplemental Figure 1', 'Extended Data Fig. 1']:
            with self.subTest(label=label):
                lines = [line(label, 500, size=12, right=210), line('Body uses a smaller font.', 529, size=10)]
                result = figure_regions(page(), lines)[0]
                self.assertIn('Body uses a smaller font.', result[1])
                self.assertEqual(result[2], [52.0, 88.0, 508.0, 455.0])

    def test_new_caption_section_table_and_font_discontinuity_stop_collection(self):
        for boundary in [line('Figure 2', 546), line('Supplementary Table 1', 546), line('Methods', 546, bold=True), line('Another style is not caption text.', 546, size=16)]:
            with self.subTest(boundary=boundary['text']):
                result = figure_regions(page(), [line('Figure 1', 500, size=12), line('Safe legend body.', 518), line('One more legend line.', 532), boundary, line('Do not absorb later content.', 564)])[0]
                self.assertIn('One more legend line.', result[1])
                self.assertNotIn(boundary['text'], result[1])
                self.assertNotIn('Do not absorb', result[1])

    def test_short_completed_line_stops_at_new_prose_but_allows_explicit_panel(self):
        base = [line('Figure 1', 500, size=12), line('A long descriptive line spans the legend width.', 518), line('Completed sentence.', 532, right=210)]
        unrelated = figure_regions(page(), base + [line('A new paragraph of article prose.', 546)])[0][1]
        self.assertNotIn('A new paragraph', unrelated)
        panel = figure_regions(page(), base + [line('(B) The next panel has its own description.', 546)])[0][1]
        self.assertIn('(B) The next panel', panel)

    def test_new_graphic_below_caption_is_not_read_as_legend_text(self):
        source = page(); source.rects = [{'x0': 60, 'top': 548, 'x1': 500, 'bottom': 650}]
        result = figure_regions(source, [line('Figure 1', 500, size=12), line('Safe legend body.', 518), line('Graph axis must not enter the legend.', 560)])[0]
        self.assertEqual(result[2], [52.0, 88.0, 508.0, 455.0])
        self.assertNotIn('Graph axis', result[1])

    def test_citation_inside_legend_is_not_mistaken_for_a_margin_footer(self):
        result = figure_regions(page(), [line('Figure 1', 500, size=12),
            line('The assay follows Example et al. doi:10.0000/synthetic.', 518),
            line('The next line explains the original synthetic controls.', 532),
            line('copyright synthetic footer', 770, size=8)])[0][1]
        self.assertIn('Example et al.', reflow_caption(result))
        self.assertIn('synthetic controls', reflow_caption(result))
        self.assertNotIn('copyright', result)

    def test_full_page_background_does_not_interrupt_caption_or_continuation(self):
        background = {'x0': 0, 'top': 0, 'x1': 600, 'bottom': 800}
        source = page(rects=[background])
        lines = [line('Figure 1', 500, size=12), line('Complete synthetic legend body.', 518)]
        result = figure_regions(source, lines)[0]
        self.assertIn('Complete synthetic legend body.', result[1])
        self.assertEqual(result[2], [52.0, 88.0, 508.0, 455.0])
        run = self.source_run()
        continued = _continue_caption(run, page(2, objects=False, rects=[background]),
            [line('The same legend continues with synthetic observations.', 80), line('A final matching caption line.', 94)])
        self.assertIsNotNone(continued)

    def test_custom_bold_section_after_completed_legend_is_not_absorbed(self):
        base = [line('Figure 1', 500, size=12), line('This full-width legend sentence is complete.', 518)]
        result = figure_regions(page(), base + [line('Patient characteristics', 534, bold=True), line('UNRELATED article paragraph follows.', 548)])[0][1]
        self.assertNotIn('Patient characteristics', result)
        self.assertNotIn('UNRELATED', result)
        panel = figure_regions(page(), base + [line('(B) Additional panel', 534, bold=True), line('More observations for this panel.', 548)])[0][1]
        self.assertIn('(B) Additional panel', panel)
        self.assertIn('More observations', panel)
        self.assertIsNone(_continue_caption(self.source_run(), page(2, objects=False),
            [line('Patient characteristics', 80, bold=True), line('UNRELATED article paragraph follows.', 94)]))

    def test_actual_pdf_two_columns_do_not_mix_legends_or_change_crops(self):
        parsed = self.parse([[('rect', 60, 100, 280, 450), ('rect', 330, 100, 550, 450),
            text('Figure 1', 500, size=12), text('Figure 2', 500, left=330, size=12),
            text('Left-only synthetic result.', 520), text('Right-only synthetic result.', 520, left=330),
            text('Left final observation.', 534), text('Right final observation.', 534, left=330)]])
        parents = [figure for figure in parsed['figures'] if not figure['parent']]
        self.assertEqual(len(parents), 2)
        self.assertIn('Left-only', parents[0]['caption']); self.assertNotIn('Right-only', parents[0]['caption'])
        self.assertIn('Right-only', parents[1]['caption']); self.assertNotIn('Left-only', parents[1]['caption'])
        self.assertEqual([figure['bbox'] for figure in parents], [[52.0, 88.0, 288.0, 455.0], [322.0, 88.0, 558.0, 455.0]])

    def source_run(self, ending='Unfinished description continues with', right=500):
        lines = [line('Figure 1', 650, size=12), line('First line explains the synthetic comparison', 672),
                 line('Second line documents the controlled observation', 690), line(ending, 708, right=right)]
        runs = []; figure_regions(page(), lines, caption_runs=runs)
        return runs[0][1]

    def test_matching_text_only_next_page_extends_unfinished_caption(self):
        run = self.source_run()
        self.assertTrue(_caption_can_continue(run))
        continuation = _continue_caption(run, page(2, objects=False), [line('additional synthetic measurements and group comparisons', 80), line('then the original legend ends.', 94)])
        self.assertIsNotNone(continuation)
        self.assertIn('additional synthetic measurements', reflow_caption('\n'.join(continuation['parts'])))
        self.assertEqual(continuation['page'], 2)

    def test_complete_bottom_sentence_does_not_absorb_next_page_article_prose(self):
        run = self.source_run('A completed description ends here.')
        continuation = _continue_caption(run, page(2, objects=False), [line('New ordinary article prose has the same size and alignment.', 80), line('More prose must not become part of the legend.', 94)])
        self.assertIsNone(continuation)

    def test_complete_sentence_allows_explicit_legend_continuation_only(self):
        run = self.source_run('A completed panel description.')
        for first in ['(D) A new panel continues the same figure legend.', 'Continued description for the same figure.', 'AB, authored baseline; CD, controlled difference.']:
            with self.subTest(first=first):
                result = _continue_caption(run, page(2, objects=False), [line(first, 80), line('Further explanation of the synthetic figure.', 94)])
                self.assertIsNotNone(result)

    def test_cross_page_geometry_new_content_and_uncertain_coordinates_are_rejected(self):
        run = self.source_run()
        normal = [line('A matching continuation line remains long enough.', 80), line('Another matching line follows.', 94)]
        cases = [
            (page(2, objects=False), [line('Methods', 80, bold=True), normal[1]]),
            (page(2, objects=False), [line('Supplementary Figure 2', 80), normal[1]]),
            (page(2, objects=False), [line('Table 1', 80), normal[1]]),
            (page(2, objects=False), [line('Shifted column.', 80, left=330, right=550), line('Another shifted line.', 94, left=330, right=550)]),
            (page(2, objects=False), [line('Different font.', 80, size=12), line('Different font again.', 94, size=12)]),
            (page(2, objects=False), [line('Starts too far down.', 240), line('Another late line.', 254)]),
            (page(2), normal), (page(2, objects=False, rotation=90), normal),
            (page(2, objects=False, cropbox=(10, 10, 590, 790)), normal),
            (page(2, objects=False, width=620), normal), (page(3, objects=False), normal),
        ]
        for index, (target, lines) in enumerate(cases):
            with self.subTest(case=index):
                self.assertIsNone(_continue_caption(run, target, lines))
        self.assertFalse(_caption_can_continue(self.source_run(right=250)))

    def test_actual_pdf_cross_page_keeps_original_figure_geometry(self):
        phrase = 'Synthetic controlled measurements explain the complete panel and the observation'
        parsed = self.parse([
            [('rect', 60, 100, 500, 600), text('Supplementary Figure 1', 630, size=12, bold=True)] + [text(phrase, y) for y in [650, 664, 678, 692]],
            [text('RUNNING HEADER', 6, size=8), text(phrase, 80), text(phrase + '.', 94), text('2', 770, left=295, size=8)],
        ])
        parents = [figure for figure in parsed['figures'] if not figure['parent']]
        self.assertEqual(len(parents), 1)
        self.assertEqual(parents[0]['caption'].count(phrase), 6)
        self.assertEqual((parents[0]['label'], parents[0]['page'], parents[0]['bbox']), ('Figure 1', 1, [52.0, 88.0, 508.0, 605.0]))
        self.assertNotIn('RUNNING HEADER', parents[0]['caption'])

    def test_subfigures_inherit_full_continued_caption_without_moving_crops(self):
        phrase = 'Original synthetic panel measurements continue with controlled observations'
        parsed = self.parse([
            [('rect', 60, 100, 500, 600), text('A', 105, left=65, bold=True), text('B', 105, left=290, bold=True),
             text('Figure 1', 630, size=12)] + [text(phrase, y) for y in [650, 664, 678, 692]],
            [text(phrase, 80), text(phrase + '.', 94)],
        ])
        figures = parsed['figures']
        self.assertEqual([(figure['id'], figure['label'], figure['parent']) for figure in figures],
                         [('f001', 'Figure 1', None), ('f002', 'Figure 1A', 'f001'), ('f003', 'Figure 1B', 'f001')])
        self.assertTrue(all(figure['page'] == 1 for figure in figures))
        self.assertTrue(all(figure['caption'] == figures[0]['caption'] for figure in figures))
        self.assertEqual(figures[0]['caption'].count(phrase), 6)
        self.assertEqual([figure['bbox'] for figure in figures],
                         [[52.0, 88.0, 508.0, 605.0], [52.0, 102.07, 282.0, 605.0], [284.0, 102.07, 508.0, 605.0]])


if __name__ == '__main__':
    unittest.main()
