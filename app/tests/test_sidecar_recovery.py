"""Damaged optional records never hide the library or get silently overwritten."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_documents import fixture_pdf, main, TestClient


class SidecarRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bioreader-sidecars-')
        root = Path(self.temp.name)
        self.patchers = [patch.object(main, 'DATA_DIR', root),
                         patch.object(main, 'PAPERS_DIR', root / 'papers'),
                         patch.object(main, 'SETTINGS_FILE', root / 'settings.json')]
        for patcher in self.patchers:
            patcher.start()
        self.client = TestClient(main.app)
        paper = self.client.post('/api/papers', files={'file': ('main.pdf', fixture_pdf())}).json()
        self.paper_id = paper['id']
        self.url = '/api/papers/' + self.paper_id
        self.directory = main.paper_dir(self.paper_id)
        self.evidence_body = {'claim': 'Check this claim', 'document_id': 'main', 'page': 1}
        self.resource_body = {'title': 'Test data', 'accession': 'GSE123'}
        self.evidence = self.client.post(self.url + '/evidence', json=self.evidence_body).json()
        self.resource = self.client.post(self.url + '/resources', json=self.resource_body).json()
        self.original = {kind: (self.directory / (kind + '.json')).read_bytes() for kind in ('evidence', 'resources')}
        self.other_id, other = main.new_paper_dir()
        main.write_meta(other, main.blank_meta(self.other_id))

    def tearDown(self):
        self.client.close()
        for patcher in self.patchers:
            patcher.stop()
        self.temp.cleanup()

    def damage(self, kind, content=b'{incomplete'):
        path = self.directory / (kind + '.json')
        path.write_bytes(content)
        return path

    def test_library_and_unrelated_summary_survive_each_damaged_sidecar(self):
        for kind in ('evidence', 'resources'):
            with self.subTest(kind=kind):
                path = self.damage(kind)
                before = path.read_bytes()
                response = self.client.get('/api/papers')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.json()), 2)
                paper = self.client.get(self.url).json()
                self.assertEqual([w['kind'] for w in paper['storage_warnings']], [kind])
                self.assertIn(kind + '.json', paper['storage_warnings'][0]['message'])
                self.assertEqual(sum(t['id'] == 'storage' for t in paper['tasks']), 1)
                self.assertIsNone(paper[kind + '_count'])
                self.assertIsNone(paper[kind + '_pending'])
                healthy = 'resources' if kind == 'evidence' else 'evidence'
                self.assertEqual(paper[healthy + '_count'], 1)
                self.assertEqual(paper[healthy + '_pending'], 1)
                self.assertEqual(self.client.get(self.url + '/' + healthy).status_code, 200)
                other = self.client.get('/api/papers/' + self.other_id).json()
                self.assertEqual(other['storage_warnings'], [])
                self.assertEqual(self.client.patch(self.url, json={'notes': 'Keep notes editable'}).status_code, 200)
                self.assertEqual(path.read_bytes(), before)
                stored = json.loads((self.directory / 'meta.json').read_text())
                self.assertNotIn('storage_warnings', stored)
                path.write_bytes(self.original[kind])

    def test_damaged_sidecars_reject_crud_without_overwriting_bytes(self):
        for kind, body, record in (('evidence', self.evidence_body, self.evidence),
                                   ('resources', self.resource_body, self.resource)):
            with self.subTest(kind=kind):
                path = self.damage(kind, b'[{}]')
                before = path.read_bytes()
                url = self.url + '/' + kind
                results = [self.client.get(url), self.client.post(url, json=body),
                           self.client.patch(url + '/' + record['id'], json={'status': 'verified'}),
                           self.client.delete(url + '/' + record['id'])]
                for result in results:
                    self.assertEqual(result.status_code, 409, result.text)
                    self.assertIn(kind + '.json', result.json()['detail'])
                self.assertEqual(path.read_bytes(), before)
                path.write_bytes(self.original[kind])

    def test_schema_invalid_duplicate_and_invalid_encoding_are_explicit(self):
        for kind in ('evidence', 'resources'):
            original = json.loads(self.original[kind])
            bad_status = {**original[0], 'status': 'invented'}
            bad_source = {**original[0], 'document_id': 'main', 'page': 'not-a-page'}
            values = [b'{broken', b'{}', b'null', b'[1]', b'[{}]', b'\xff',
                      json.dumps(original + original).encode(), json.dumps([bad_status]).encode(),
                      json.dumps([bad_source]).encode()]
            for content in values:
                with self.subTest(kind=kind, content=content[:35]):
                    path = self.damage(kind, content)
                    self.assertEqual(self.client.get('/api/papers').status_code, 200)
                    result = self.client.get(self.url + '/' + kind)
                    self.assertEqual(result.status_code, 409, result.text)
                    self.assertEqual(path.read_bytes(), content)
            path.write_bytes(self.original[kind])

    def test_global_resource_index_never_silently_omits_damaged_resources(self):
        self.damage('resources')
        result = self.client.get('/api/resources')
        self.assertEqual(result.status_code, 409)
        self.assertIn('resources.json', result.json()['detail'])
        (self.directory / 'resources.json').write_bytes(self.original['resources'])
        self.damage('evidence')
        # Broken evidence does not remove any resource entries from the index.
        result = self.client.get('/api/resources')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(result.json()), 1)

    def test_exports_refuse_to_silently_drop_damaged_records(self):
        for kind in ('evidence', 'resources'):
            path = self.damage(kind)
            for route in ('/notes/export-preview', '/export.zip'):
                result = self.client.post(self.url + route, json={'notes': 'My note'})
                self.assertEqual(result.status_code, 409, result.text)
                self.assertIn(kind + '.json', result.json()['detail'])
            self.assertEqual(self.client.get(self.url + '/export.md').status_code, 409)
            path.write_bytes(self.original[kind])

    def test_restored_file_clears_warning_without_restart(self):
        for kind in ('evidence', 'resources'):
            self.damage(kind)
        damaged = self.client.get(self.url).json()
        self.assertEqual(len(damaged['storage_warnings']), 2)
        self.assertEqual(sum(t['id'] == 'storage' for t in damaged['tasks']), 1)
        for kind in ('evidence', 'resources'):
            (self.directory / (kind + '.json')).write_bytes(self.original[kind])
        restored = self.client.get(self.url).json()
        self.assertEqual(restored['storage_warnings'], [])
        self.assertNotIn('storage', {t['id'] for t in restored['tasks']})
        self.assertEqual(restored['evidence_count'], 1)
        self.assertEqual(restored['resources_count'], 1)


if __name__ == '__main__':
    unittest.main()
