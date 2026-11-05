import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_documents import main, TestClient


class InterfacePreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='bioreader-ui-preferences-')
        self.directory = Path(self.temporary.name)
        self.patcher = patch.object(main, 'DATA_DIR', self.directory)
        self.patcher.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close(); self.patcher.stop(); self.temporary.cleanup()

    def test_default_and_persistence_do_not_touch_models_or_library(self):
        settings = self.directory / 'settings.json'
        original = b'{"model":"synthetic-model","api_key":"synthetic-key"}'
        settings.write_bytes(original)
        self.assertEqual(self.client.get('/api/ui-preferences').json(), {'locale': 'zh-CN'})
        self.assertFalse((self.directory / 'ui-preferences.json').exists())
        response = self.client.put('/api/ui-preferences', json={'locale': 'en'})
        self.assertEqual(response.status_code, 200)
        with TestClient(main.app) as restarted:
            self.assertEqual(restarted.get('/api/ui-preferences').json(), {'locale': 'en'})
        self.assertEqual(settings.read_bytes(), original)
        self.assertFalse((self.directory / 'papers').exists())

    def test_invalid_language_and_unrelated_settings_are_rejected(self):
        for body in ({'locale': 'fr'}, {'locale': 'en', 'api_key': 'unrelated'}, {'locale': None}):
            self.assertEqual(self.client.put('/api/ui-preferences', json=body).status_code, 422)
        self.assertFalse((self.directory / 'ui-preferences.json').exists())

    def test_corrupt_preferences_are_preserved(self):
        target = self.directory / 'ui-preferences.json'
        for content in (b'not-json', b'[]', b'{"locale":"invalid"}'):
            target.write_bytes(content)
            self.assertEqual(self.client.get('/api/ui-preferences').status_code, 409)
            self.assertEqual(self.client.put('/api/ui-preferences', json={'locale': 'en'}).status_code, 409)
            self.assertEqual(target.read_bytes(), content)

    def test_failed_atomic_replace_keeps_the_saved_language(self):
        self.client.put('/api/ui-preferences', json={'locale': 'zh-CN'})
        import reading_workflow
        with patch.object(reading_workflow.os, 'replace', side_effect=OSError('synthetic disk error')):
            with self.assertRaises(OSError):
                self.client.put('/api/ui-preferences', json={'locale': 'en'})
        self.assertEqual(self.client.get('/api/ui-preferences').json(), {'locale': 'zh-CN'})
        self.assertEqual([p.name for p in self.directory.iterdir()], ['ui-preferences.json'])


if __name__ == '__main__':
    unittest.main()
