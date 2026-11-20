"""Saved figures and captured PDF evidence survive retries and respect paper/file identity."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_documents import fixture_pdf, main, TestClient


class FigureShelfTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        for name, path in [('DATA_DIR', root), ('PAPERS_DIR', root / 'papers'), ('TRASH_DIR', root / 'trash')]:
            self.enterContext(patch.object(main, name, path))
        self.client = self.enterContext(TestClient(main.app))
        self.first = self.upload('First')
        self.second = self.upload('Second')

    def upload(self, title):
        result = self.client.post('/api/papers', files={'file': (title + '.pdf', fixture_pdf(marker=title), 'application/pdf')})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()['id']

    def shelf(self, ids, query='', **more):
        response = self.client.post('/api/figure-shelf/query', json={'paper_ids': ids, 'query': query, **more})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_saved_figures_scope_search_tags_and_source_revalidation(self):
        papers = [self.first, self.second]
        for pid in papers:
            url = '/api/papers/' + pid + '/figures'
            f = self.client.get(url).json()[0]
            result = self.client.patch(url + '/' + f['id'], json={'favorite': True, 'tags': ['  CAF ', 'caf', '空间']})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['tags'], ['CAF', '空间'])
        found = self.shelf([*papers, self.first], 'CAF 空间', limit=1)
        self.assertEqual((found['total'], len(found['figures'])), (2, 1))
        row = found['figures'][0]
        self.assertEqual(row['paper_id'], self.first)
        self.assertEqual(self.shelf([self.second], '空间')['figures'][0]['paper_id'], self.second)
        self.assertEqual(self.shelf([], '空间')['total'], 0)
        self.assertEqual(self.shelf(papers, 'none')['total'], 0)
        url = '/api/papers/' + self.first + '/figures/' + row['id']
        source = url + '/shelf-source?key=' + row['shelf_key']
        self.assertEqual(self.client.get(source).json()['page'], row['page'])
        self.assertEqual(self.client.patch(url, json={'tags': ['x' * 41]}).status_code, 422)
        self.assertEqual(self.client.patch(url, json={'expected_figure_source': ['main', 2, []], 'favorite': False}).status_code, 409)
        extracted = self.client.post('/api/papers/' + self.first + '/figures/extract')
        self.assertEqual(extracted.status_code, 200, extracted.text)
        self.assertEqual(self.client.get(source).json()['tags'], ['CAF', '空间'])
        self.client.patch(url, json={'favorite': False})
        self.assertEqual(self.shelf([self.first])['total'], 0)
        self.client.patch(url, json={'favorite': True})
        self.assertEqual(self.client.get(source).status_code, 404)

    def test_supplement_selection_and_saved_source_invalidation(self):
        url = '/api/papers/' + self.first
        sup = self.client.post(url + '/supplements', files={'file': ('supp.pdf', fixture_pdf('Figure S2', 'Supplement'), 'application/pdf')}).json()['supplements'][0]
        doc = sup['id']
        fig = next(f for f in self.client.get(url + '/figures').json() if f['document_id'] == doc)
        self.client.patch(url + '/figures/' + fig['id'], json={'favorite': True})
        saved = self.shelf([self.first])['figures'][0]
        self.assertEqual((saved['document_id'], saved['document_name']), (doc, 'supp.pdf'))
        revision = self.client.get(url).json()['document_revisions'][doc]
        evidence = {'claim': 'Check sampling', 'quote': 'Supplement', 'document_id': doc, 'page': 1,
                    'selection_bbox': [.1, .2, .3, .4], 'expected_source_revision': revision}
        added = self.client.post(url + '/evidence', json=evidence)
        self.assertEqual(added.status_code, 200, added.text)
        self.assertEqual(added.json()['selection_bbox'], evidence['selection_bbox'])
        self.assertEqual(added.json()['status'], 'pending')
        self.assertEqual(self.client.post(url + '/evidence', json={**evidence, 'expected_source_revision': 'outdated'}).status_code, 409)
        self.assertEqual(self.client.post(url + '/evidence', json={**evidence, 'selection_bbox': [0, 0, 2, 2]}).status_code, 422)
        updated = self.client.patch(url + '/evidence/' + added.json()['id'], json={'quote': 'Changed passage'})
        self.assertNotIn('selection_bbox', updated.json())
        self.client.delete(url + '/supplements/' + doc)
        self.assertEqual(self.shelf([self.first])['total'], 0)
        self.assertEqual(self.client.get(url + '/figures/' + fig['id'] + '/shelf-source', params={'key': saved['shelf_key']}).status_code, 404)

    def test_changed_source_cannot_reassign_a_manual_crop(self):
        url = '/api/papers/' + self.first
        cropped = self.client.post(url + '/figures/crop', json={'page': 1, 'bbox': [60, 40, 220, 180]}).json()
        endpoint = url + '/figures/' + cropped['id']
        self.assertEqual(self.client.patch(endpoint, json={'favorite': True}).status_code, 200)
        evidence = {'claim': 'Historical figure observation', 'quote': 'Marker location', 'page': 1,
                    'document_id': 'main', 'figure_id': cropped['id'], 'status': 'verified'}
        original = self.client.post(url + '/evidence', json=evidence)
        self.assertEqual(original.status_code, 200, original.text)
        self.client.post(url + '/file', files={'file': ('replacement.pdf', fixture_pdf(marker='Replacement'), 'application/pdf')})
        self.assertFalse(self.shelf([self.first])['figures'][0]['source_available'])
        historical = self.client.get(url + '/evidence').json()[0]
        self.assertEqual((historical['quote'], historical['status']), ('Marker location', 'pending'))
        self.assertTrue(historical['source_changed'])
        record = url + '/evidence/' + original.json()['id']
        self.assertEqual(self.client.post(url + '/evidence', json={**evidence, 'status': 'pending'}).status_code, 409)
        self.assertEqual(self.client.patch(record, json={'status': 'verified'}).status_code, 409)
        fresh = self.client.post(url + '/figures/crop', json={'page': 1, 'bbox': [60, 40, 220, 180]}).json()
        verified = self.client.patch(record, json={'figure_id': fresh['id'], 'status': 'verified'})
        self.assertEqual(verified.status_code, 200, verified.text)
        self.assertFalse(verified.json()['source_changed'])
        self.assertEqual(self.client.patch(endpoint, json={'favorite': False}).status_code, 200)
        self.assertEqual(self.client.patch(endpoint, json={'favorite': True}).status_code, 409)

    def test_changed_pdf_never_uses_same_page_in_new_file_as_saved_source(self):
        url = '/api/papers/' + self.first
        fig = self.client.get(url + '/figures').json()[0]
        self.client.patch(url + '/figures/' + fig['id'], json={'favorite': True})
        old = self.shelf([self.first])['figures'][0]
        response = self.client.post(url + '/file', files={'file': ('changed.pdf', fixture_pdf(marker='Other'), 'application/pdf')})
        self.assertEqual(response.status_code, 200, response.text)
        rows = self.shelf([self.first])['figures']
        self.assertTrue(all(not item['source_available'] and not item['has_image'] for item in rows))
        response = self.client.get(url + '/figures/' + fig['id'] + '/shelf-source', params={'key': old['shelf_key']})
        self.assertIn(response.status_code, (404, 409))
        self.assertEqual(self.shelf(['f' * 32, self.first])['skipped'], 1)
        meta = main.PAPERS_DIR / self.second / 'meta.json'
        meta.write_text(json.dumps({'schema_version': main.PAPER_SCHEMA_VERSION + 1}))
        self.assertEqual(self.shelf([self.second])['skipped'], 1)


if __name__ == '__main__':
    unittest.main()
