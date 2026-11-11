"""Future-version paper data is refused instead of misread; legacy data is stamped on save."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_documents import fixture_pdf, main, TestClient


class SchemaVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bioreader-schema-')
        root = Path(self.temp.name)
        self.patchers = [patch.object(main, 'DATA_DIR', root),
                         patch.object(main, 'PAPERS_DIR', root / 'papers'),
                         patch.object(main, 'TRASH_DIR', root / 'trash'),
                         patch.object(main, 'SETTINGS_FILE', root / 'settings.json')]
        for patcher in self.patchers:
            patcher.start()
        self.client = TestClient(main.app)
        response = self.client.post('/api/papers', files={'file': ('main.pdf', fixture_pdf())})
        self.assertEqual(response.status_code, 200, response.text)
        self.paper = response.json()
        self.url = '/api/papers/' + self.paper['id']
        self.directory = main.paper_dir(self.paper['id'])

    def tearDown(self):
        self.client.close()
        for patcher in self.patchers:
            patcher.stop()
        self.temp.cleanup()

    def _raw(self):
        return json.loads((self.directory / 'meta.json').read_text(encoding='utf-8'))

    def _write_raw(self, raw):
        (self.directory / 'meta.json').write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')

    def _future_raw(self):
        raw = self._raw()
        raw['schema_version'] = main.PAPER_SCHEMA_VERSION + 1
        self._write_raw(raw)
        return raw

    def test_new_papers_carry_the_current_schema_version(self):
        self.assertEqual(self._raw()['schema_version'], main.PAPER_SCHEMA_VERSION)

    def test_legacy_meta_reads_normally_and_is_stamped_on_next_save(self):
        raw = self._raw()
        raw.pop('schema_version')
        self._write_raw(raw)
        self.assertEqual(self.client.get(self.url).status_code, 200, self.client.get(self.url).text)
        self.client.patch(self.url, json={'tags': ['回填']})
        self.assertEqual(self._raw()['schema_version'], main.PAPER_SCHEMA_VERSION)

    def test_future_version_refused_on_read_and_flagged_in_library(self):
        self._future_raw()
        refused = self.client.get(self.url)
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertIn('更新版本', refused.json()['detail'])
        listing = self.client.get('/api/papers').json()
        self.assertEqual([p['id'] for p in listing], [self.paper['id']])
        self.assertTrue(listing[0]['schema_incompatible'])
        self.assertEqual(listing[0]['title'], self.paper['title'])
        # Derived annotations are never computed for refused papers.
        self.assertNotIn('resources_count', listing[0])

    def test_future_version_refused_on_write(self):
        raw = self._future_raw()
        with self.assertRaises(main.HTTPException) as raised:
            main.write_meta(self.directory, raw)
        self.assertEqual(raised.exception.status_code, 409)
        # The refused write leaves the stored file untouched.
        self.assertEqual(self._raw()['schema_version'], main.PAPER_SCHEMA_VERSION + 1)

    def test_stale_meta_cannot_overwrite_a_future_version_on_disk(self):
        stale = self._raw()
        self._future_raw()
        path = self.directory / 'meta.json'
        before = path.read_bytes()
        with self.assertRaises(main.HTTPException) as raised:
            main.write_meta(self.directory, stale)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(path.read_bytes(), before)

    def test_paper_scoped_endpoints_refuse_future_version(self):
        self._future_raw()
        for path in ('', '/text', '/figures', '/evidence'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(self.url + path).status_code, 409)

    def test_library_search_reports_future_version_without_failing(self):
        self._future_raw()
        result = self.client.get('/api/library/search', params={'q': 'synthetic'})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['warning_count'], 1)
        self.assertIn('更新版本', result.json()['warnings'][0]['message'])

    def test_resource_scans_warn_and_skip_future_data_without_deriving(self):
        self._future_raw()
        paths = (('/api/resources', 'application/json'),
                 ('/api/resources/export?format=csv', 'text/csv'),
                 ('/api/resources/export?format=accessions', 'text/plain'))
        with patch.object(main, 'annotate_paper') as annotate, patch.object(main, 'public_resources') as resources:
            for path, content_type in paths:
                with self.subTest(path=path):
                    with self.assertLogs(main.__name__, level='WARNING') as warnings:
                        result = self.client.get(path)
                        self.assertEqual(result.status_code, 200, result.text)
                        self.assertEqual(result.headers['content-type'].split(';')[0], content_type)
                        if content_type == 'application/json':
                            self.assertEqual(result.json(), [])
                        elif content_type == 'text/csv':
                            rows = result.content.decode('utf-8-sig').splitlines()
                            self.assertEqual(len(rows), 1)
                            self.assertTrue(rows[0].startswith('论文标题,'))
                        else:
                            self.assertEqual(result.text, '')
                        annotate.assert_not_called()
                        resources.assert_not_called()
                    self.assertTrue(any(self.paper['id'] in message for message in warnings.output))


if __name__ == '__main__':
    unittest.main()
