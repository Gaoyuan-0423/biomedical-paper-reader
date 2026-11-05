"""Upgrade legacy short automatic captions without replacing research records."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from test_documents import fixture_pdf, main, TestClient


def supplementary_pdf(number):
    writer = PdfWriter()
    writer.add_page(PdfReader(io.BytesIO(fixture_pdf())).pages[0])
    stream = DecodedStreamObject()
    stream.set_data((
        '0.15 0.4 0.6 rg 60 360 400 200 re f 0 0 0 rg '
        f'BT /F1 12 Tf 60 330 Td (Supplementary Figure {number}) Tj ET '
        'BT /F1 9.5 Tf 60 312 Td (A. Synthetic sample groups are compared.) Tj '
        '0 -14 Td (B. Error bars represent the synthetic standard deviation.) Tj ET'
    ).encode('ascii'))
    writer.pages[0][NameObject('/Contents')] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


class CaptionReextractionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='bioreader-caption-upgrade-')
        self.addCleanup(self.temporary.cleanup)
        directory = Path(self.temporary.name)
        for name, value in [('DATA_DIR', directory), ('PAPERS_DIR', directory / 'papers'),
                            ('SETTINGS_FILE', directory / 'settings.json')]:
            patcher = patch.object(main, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        response = self.client.post('/api/papers', files={'file': ('main.pdf', fixture_pdf(), 'application/pdf')})
        self.assertEqual(response.status_code, 200, response.text)
        self.url = '/api/papers/' + response.json()['id']
        self.directory = main.paper_dir(response.json()['id'])
        self.supplements = []
        for number in (1, 2):
            response = self.client.post(self.url + '/supplements', files={
                'file': (f'synthetic-supplement-{number}.pdf', supplementary_pdf(number), 'application/pdf')})
            self.assertEqual(response.status_code, 200, response.text)
            self.supplements.append(response.json()['supplements'][-1]['id'])

    def test_old_automatic_caption_upgrades_in_place_and_preserves_all_other_research(self):
        document_id = self.supplements[0]
        records = self.client.get(self.url + '/figures').json()
        figure = next(item for item in records if item['document_id'] == document_id and not item.get('parent'))
        figure['caption'] = 'Supplementary Figure 1'  # Persisted by the old extractor.
        figure['observation'] = 'Previously saved observation, independent of the caption.'
        main.save_figures(self.directory, records)
        note = f'# Existing note\n\n![Saved figure](figure:{figure["id"]})\n\nKeep my wording.'
        self.client.patch(self.url, json={'notes': note})
        cropped = self.client.post(self.url + '/figures/crop', json={
            'document_id': document_id, 'page': 1, 'bbox': [65, 240, 200, 360],
            'label': 'My crop', 'caption': 'My manually written caption'}).json()
        before = self.client.get(self.url + '/figures').json()
        pixels = self.client.get(self.url + '/figures/' + figure['id'] + '.png').content

        for _ in range(2):  # A second extraction must be idempotent.
            response = self.client.post(self.url + '/figures/extract', params={'document_id': document_id})
            self.assertEqual(response.status_code, 200, response.text)
            after = response.json()['figures']
            self.assertEqual({item['id'] for item in after}, {item['id'] for item in before})
            upgraded = next(item for item in after if item['id'] == figure['id'])
            self.assertIn('Synthetic sample groups are compared.', upgraded['caption'])
            self.assertIn('Error bars represent the synthetic standard deviation.', upgraded['caption'])
            for key in ('label', 'page', 'bbox', 'document_id', 'parent', 'observation'):
                self.assertEqual(upgraded[key], figure.get(key), key)
            for untouched in before:
                if untouched['document_id'] != document_id or untouched['id'] == cropped['id']:
                    self.assertEqual(next(item for item in after if item['id'] == untouched['id']), untouched)
            self.assertEqual(self.client.get(self.url).json()['notes'], note)
            self.assertEqual(self.client.get(self.url + '/figures/' + figure['id'] + '.png').content, pixels)

    def test_edited_or_deliberately_empty_caption_is_not_overwritten_by_new_extractor(self):
        for document_id, manual_caption in zip(self.supplements, ('My revised caption.', '')):
            figure = next(item for item in self.client.get(self.url + '/figures').json()
                          if item['document_id'] == document_id and not item.get('parent'))
            response = self.client.patch(self.url + '/figures/' + figure['id'], json={
                'caption': manual_caption, 'observation': 'My observation'})
            self.assertEqual(response.status_code, 200, response.text)
            response = self.client.post(self.url + '/figures/extract', params={'document_id': document_id})
            self.assertEqual(response.status_code, 200, response.text)
            kept = next(item for item in response.json()['figures'] if item['id'] == figure['id'])
            self.assertEqual(kept['caption'], manual_caption)
            self.assertTrue(kept['caption_edited'])
            self.assertEqual(kept['observation'], 'My observation')


if __name__ == '__main__':
    unittest.main()
