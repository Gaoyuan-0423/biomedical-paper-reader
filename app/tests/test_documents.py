"""Separate source PDFs, real figure crops, combined reading and portable exports."""
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('BIOREADER_DATA_DIR', tempfile.mkdtemp(prefix='bioreader-doc-tests-'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
import main
from llm import build_messages
from note_assets import illustrate_notes, markdown_bundle


def fixture_pdf(label='Figure 1', marker='MAIN'):
    w = PdfWriter()
    p = w.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    p[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): w._add_object(font)})})
    stream = DecodedStreamObject()
    text = f'BT /F1 12 Tf 60 740 Td ({marker} synthetic paper for source isolation.) Tj '
    for i in range(4):
        text += '0 -18 Td (A controlled test of sample groups, methods, outcomes and limitations.) Tj '
    text += f'ET 0.15 0.4 0.6 rg 60 360 400 200 re f 0 0 0 rg BT /F1 11 Tf 60 330 Td ({label}. Synthetic result from {marker}.) Tj ET'
    stream.set_data(text.encode())
    p[NameObject('/Contents')] = w._add_object(stream)
    out = io.BytesIO(); w.write(out)
    return out.getvalue()


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bioreader-documents-')
        d = Path(self.temp.name)
        self.patchers = [patch.object(main, 'DATA_DIR', d), patch.object(main, 'PAPERS_DIR', d / 'papers'), patch.object(main, 'SETTINGS_FILE', d / 'settings.json')]
        for p in self.patchers: p.start()
        self.client = TestClient(main.app)
        self.primary = fixture_pdf()
        r = self.client.post('/api/papers', files={'file': ('main.pdf', self.primary, 'application/pdf')})
        self.assertEqual(r.status_code, 200, r.text)
        self.paper = r.json(); self.url = '/api/papers/' + self.paper['id']
        self.directory = main.paper_dir(self.paper['id'])

    def tearDown(self):
        self.client.close()
        for p in self.patchers: p.stop()
        self.temp.cleanup()

    def attach(self):
        r = self.client.post(self.url + '/supplements', files={'file': ('supp.pdf', fixture_pdf('Supplementary Figure S1', 'SUPPLEMENT'), 'application/pdf')})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['supplements'][0]

    def test_sources_are_separate_and_joint_reading_preserves_origin(self):
        original_figs = self.client.get(self.url + '/figures').json()
        self.assertTrue(original_figs, 'real geometry extraction must find the fixture figure')
        self.client.patch(self.url, json={'notes': 'Existing note'})
        supplement = self.attach()
        self.assertEqual(self.client.get(self.url + '/file').content, self.primary)
        self.assertNotEqual(self.client.get(self.url + '/file', params={'document_id': supplement['id']}).content, self.primary)
        meta = self.client.get(self.url).json()
        self.assertEqual(meta['notes'], 'Existing note')
        text = main.reading_text(self.directory, meta)
        self.assertIn('MAIN synthetic', text); self.assertIn('SUPPLEMENT synthetic', text)
        self.assertIn('[Page 2] 补充材料 supp.pdf 原文件第 1 页', text)
        figs = self.client.get(self.url + '/figures').json()
        self.assertGreater(len(figs), len(original_figs))
        self.assertEqual(len({f['id'] for f in figs}), len(figs))
        self.assertTrue(any(f.get('document_id') == supplement['id'] for f in figs))
        for f in figs:
            self.assertEqual(self.client.get(self.url + '/figures/' + f['id'] + '.png').status_code, 200)

    def test_duplicate_invalid_and_path_traversal_do_not_change_sources(self):
        self.attach()
        before = self.client.get(self.url + '/figures').json()
        for data, expected in ((fixture_pdf('Supplementary Figure S1', 'SUPPLEMENT'), 409), (b'%PDF-broken', 400), (self.primary, 409)):
            r = self.client.post(self.url + '/supplements', files={'file': ('bad.pdf', data)})
            self.assertEqual(r.status_code, expected)
        self.assertEqual(self.client.get(self.url + '/figures').json(), before)
        self.assertEqual(len(self.client.get(self.url).json()['supplements']), 1)
        self.assertEqual(self.client.get(self.url + '/file', params={'document_id': '../settings'}).status_code, 404)
        self.assertEqual(self.client.get(self.url + '/file').content, self.primary)

    def test_reextract_preserves_each_source_ids_captions_and_manual_crops(self):
        sup = self.attach()
        figures = self.client.get(self.url + '/figures').json()
        fig = next(f for f in figures if f.get('document_id') == sup['id'])
        self.client.patch(self.url + '/figures/' + fig['id'], json={'caption': 'Edited caption', 'observation': 'Observed'})
        crop = self.client.post(self.url + '/figures/crop', json={'document_id': sup['id'], 'page': 1, 'bbox': [60, 232, 460, 432]}).json()
        self.assertEqual(crop['document_id'], sup['id'])
        r = self.client.post(self.url + '/figures/extract', params={'document_id': sup['id']})
        self.assertEqual(r.status_code, 200, r.text)
        revised = {f['id']: f for f in r.json()['figures']}
        self.assertEqual(revised[fig['id']]['caption'], 'Edited caption')
        self.assertEqual(revised[fig['id']]['observation'], 'Observed')
        self.assertIn(crop['id'], revised)
        self.assertEqual(self.client.get(self.url + '/figures/' + crop['id'] + '.png').status_code, 200)
        # Replacing the main PDF must not delete supplementary images.
        self.client.post(self.url + '/file', files={'file': ('new-main.pdf', fixture_pdf('Figure 2', 'REPLACEMENT'))})
        self.assertEqual(self.client.get(self.url + '/figures/' + fig['id'] + '.png').status_code, 200)
        self.assertIn(sup['id'], {f.get('document_id') for f in self.client.get(self.url + '/figures').json()})

    def test_generation_automatically_inserts_main_and_supplement_figures(self):
        self.attach()
        main.save_settings({**main.DEFAULT_SETTINGS, 'api_key': 'synthetic-not-a-key'})
        captured = []
        async def fake_stream(*args, **kwargs):
            captured.extend(args[3]); yield '# Reading\n\n### Figure 1 | Main finding\n\nSynthetic text.'
        with patch.object(main, 'stream_chat', fake_stream):
            r = self.client.post(self.url + '/read?use_figures=1')
        events = [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith('data:')]
        self.assertEqual(events[-1]['type'], 'done')
        notes = events[-1]['notes']
        figures = [f for f in self.client.get(self.url + '/figures').json() if not f.get('parent')]
        for f in figures:
            self.assertIn('(figure:' + f['id'] + ')', notes)
            self.assertIn('(figure:' + f['id'] + ')', captured[-1]['content'])
        self.assertIn('SUPPLEMENT synthetic', captured[-1]['content'])
        self.assertEqual(self.client.get(self.url).json()['notes'], notes)
        bundle = self.client.post(self.url + '/export.zip', json={'notes': notes})
        self.assertEqual(bundle.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as z:
            report = z.read('report.md').decode()
            self.assertNotIn('figure:', report)
            for f in figures:
                self.assertIn('assets/' + f['id'] + '.png', report)
                self.assertTrue(z.read('assets/' + f['id'] + '.png').startswith(b'\x89PNG'))

    def test_existing_notes_can_be_illustrated_without_model_or_duplicate_images(self):
        self.attach()
        notes = '# Existing note\n\nOriginal analysis.'
        r = self.client.post(self.url + '/notes/illustrate', json={'notes': notes})
        self.assertEqual(r.status_code, 200)
        illustrated = r.json()['notes']
        self.assertIn('Original analysis.', illustrated); self.assertIn('figure:', illustrated)
        self.assertEqual(self.client.post(self.url + '/notes/illustrate', json={'notes': illustrated}).json()['notes'], illustrated)
        self.assertEqual(self.client.post(self.url + '/export.zip', json={'notes': '![missing](figure:f999999)'}).status_code, 400)

    def test_removing_supplement_keeps_existing_note_images_and_never_reuses_ids(self):
        sup = self.attach()
        figs = self.client.get(self.url + '/figures').json()
        old = next(f for f in figs if f.get('document_id') == sup['id'])
        r = self.client.delete(self.url + '/supplements/' + sup['id'])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get(self.url + '/file', params={'document_id': sup['id']}).status_code, 404)
        self.assertEqual(self.client.get(self.url + '/figures/' + old['id'] + '.png').status_code, 200)
        self.attach()
        new_ids = {f['id'] for f in self.client.get(self.url + '/figures').json()}
        self.assertNotIn(old['id'], new_ids)

    def test_quick_reading_only_illustrates_mentioned_figures(self):
        figs = [{'id': 'f001', 'label': 'Figure 1'}, {'id': 'f002', 'label': 'Figure 10'}]
        notes = illustrate_notes('## Figure 10\n\nSelected result.', figs, 'quick')
        self.assertIn('figure:f002', notes); self.assertNotIn('figure:f001', notes)
        self.assertNotIn('figure:f999', illustrate_notes('![Invented](figure:f999)', figs))


if __name__ == '__main__':
    unittest.main()
