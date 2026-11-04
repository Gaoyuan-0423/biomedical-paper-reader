"""Library search navigation and filtered exports through the actual API."""
import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_documents import fixture_pdf, main, TestClient


class LibraryToolsAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bioreader-library-tools-')
        data = Path(self.temp.name)
        self.patchers = [patch.object(main, 'DATA_DIR', data), patch.object(main, 'PAPERS_DIR', data / 'papers'),
                         patch.object(main, 'SETTINGS_FILE', data / 'settings.json')]
        for item in self.patchers:
            item.start()
        self.client = TestClient(main.app)
        response = self.client.post('/api/papers', files={'file': ('main.pdf', fixture_pdf('Figure 1', 'NEEDLEMAIN'))})
        self.assertEqual(response.status_code, 200, response.text)
        self.paper = response.json()
        self.url = '/api/papers/' + self.paper['id']

    def tearDown(self):
        self.client.close()
        for item in self.patchers:
            item.stop()
        self.temp.cleanup()

    def resource(self, **fields):
        response = self.client.post(self.url + '/resources', json={
            'title': 'Training cohorts', 'identifiers': 'GSE123; GSE456', 'purpose': 'training',
            'organism': 'human', 'tissue': 'stomach', 'disease': 'gastric cancer',
            'modality': 'scRNA-seq', 'platform': '10x', 'sample_count': '6 patients; 12 specimens',
            'sample_groups': 'paired tumor and normal', **fields})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_filter_export_scope_and_csv_content_match_saved_records(self):
        self.resource()
        self.resource(title='Validation', identifiers='GSE789', organism='mouse')
        original = (main.paper_dir(self.paper['id']) / 'resources.json').read_bytes()
        params = {'q': 'training', 'organism': 'HUMAN', 'tissue': 'stomach', 'platform': '10x'}
        results = self.client.get('/api/resources', params=params).json()
        self.assertEqual(len(results), 1)
        response = self.client.get('/api/resources/export', params={**params, 'format': 'csv', 'paper_id': self.paper['id']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
        self.assertIn('attachment;', response.headers['content-disposition'])
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(len(rows), 2)
        self.assertIn('6 patients; 12 specimens', rows[1])
        self.assertIn('paired tumor and normal', rows[1])
        self.assertNotIn('GSE789', response.text)
        ids = self.client.get('/api/resources/export', params={**params, 'format': 'accessions'}).text.splitlines()
        self.assertEqual(ids, ['GSE123', 'GSE456'])
        self.assertEqual((main.paper_dir(self.paper['id']) / 'resources.json').read_bytes(), original)

    def test_export_never_reads_another_scope_or_infers_accessions_from_notes(self):
        self.resource(identifiers='GSE123', notes='Future suggestion: GSE999')
        other = self.client.post('/api/papers', files={'file': ('other.pdf', fixture_pdf('Figure 2', 'SECOND'))}).json()
        self.client.post('/api/papers/' + other['id'] + '/resources', json={'title': 'Other', 'accession': 'GSE222'})
        response = self.client.get('/api/resources/export', params={'format': 'accessions', 'paper_id': self.paper['id']})
        self.assertEqual(response.text.splitlines(), ['GSE123'])
        self.assertEqual(self.client.get('/api/resources/export', params={'format': 'bad'}).status_code, 422)
        self.assertEqual(self.client.get('/api/resources/export', params={'format': 'csv', 'paper_id': '../outside'}).status_code, 404)
        (main.paper_dir(self.paper['id']) / 'resources.json').write_text('broken')
        self.assertEqual(self.client.get('/api/resources/export', params={'format': 'csv'}).status_code, 409)

    def test_current_resource_filters_share_unicode_rules_and_export_scope(self):
        self.resource(tissue='ᾈ', title='Unicode tissue')
        params = {'tissue': 'ἀι'}
        current = self.client.get(self.url + '/resources', params=params).json()
        global_rows = self.client.get('/api/resources', params=params).json()
        self.assertEqual(len(current), 1)
        self.assertEqual([item['id'] for item in current], [item['id'] for item in global_rows])
        export = self.client.get('/api/resources/export', params={**params, 'paper_id': self.paper['id'], 'format': 'csv'})
        self.assertIn('Unicode tissue', export.text)
        self.assertEqual(self.client.get(self.url + '/resources', params={'organism': 'mouse'}).json(), [])

    def test_pdf_search_source_is_verified_and_removed_material_rejected(self):
        response = self.client.get('/api/library/search', params={'q': 'NEEDLEMAIN', 'scope': 'pdf'})
        self.assertEqual(response.status_code, 200, response.text)
        hit = response.json()['items'][0]
        self.assertEqual(hit['paper_id'], self.paper['id'])
        self.assertEqual(hit['page'], 1)
        source = self.client.post(self.url + '/search/source', json=hit['source'])
        self.assertEqual(source.status_code, 200, source.text)
        forged = {**hit['source'], 'quote': 'TEXT NEVER PRESENT IN ORIGINAL'}
        self.assertEqual(self.client.post(self.url + '/search/source', json=forged).status_code, 409)
        (main.paper_dir(self.paper['id']) / 'paper.pdf').unlink()
        self.assertIn(self.client.post(self.url + '/search/source', json=hit['source']).status_code, (404, 409))

    def test_search_saved_notes_and_supplements_are_distinct(self):
        self.client.patch(self.url, json={'notes': 'A note with NEEDLENOTE and no PDF page.'})
        supplement = self.client.post(self.url + '/supplements', files={'file': ('supp.pdf', fixture_pdf('Figure S1', 'NEEDLESUPP'))}).json()['supplements'][0]
        notes = self.client.get('/api/library/search', params={'q': 'needlenote', 'scope': 'notes'}).json()['items']
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]['kind'], 'notes')
        self.assertIsNone(notes[0].get('page'))
        hits = self.client.get('/api/library/search', params={'q': 'NEEDLESUPP', 'scope': 'pdf'}).json()['items']
        self.assertTrue(hits)
        self.assertEqual(hits[0]['document_id'], supplement['id'])
        self.assertEqual(self.client.get('/api/library/search', params={'q': 'n'}).status_code, 422)
        self.assertEqual(self.client.get('/api/library/search', params={'q': '  '}).status_code, 422)

    def test_corrupt_metadata_is_reported_without_hiding_healthy_hits(self):
        directory = main.PAPERS_DIR / ('c' * 32)
        directory.mkdir()
        (directory / 'meta.json').write_text('{broken')
        response = self.client.get('/api/library/search', params={'q': 'NEEDLEMAIN'}).json()
        self.assertTrue(response['items'])
        self.assertGreaterEqual(response['warning_count'], 1)
        self.assertTrue(any(item['paper_id'] == directory.name for item in response['warnings']))
        for corrupt in ('[]', 'null', '{"id":"wrong","supplements":[]}'):
            (directory / 'meta.json').write_text(corrupt)
            retry = self.client.get('/api/library/search', params={'q': 'NEEDLEMAIN'})
            self.assertEqual(retry.status_code, 200, retry.text)
            self.assertTrue(retry.json()['items'])
            self.assertGreaterEqual(retry.json()['warning_count'], 1)


if __name__ == '__main__':
    unittest.main()
