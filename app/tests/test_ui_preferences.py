import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from test_documents import main, TestClient


DEFAULTS = {'locale': 'zh-CN', 'scheme': 'system', 'accent': 'blue',
            'ui_font_size': 13, 'note_font_size': 14}


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
        self.assertEqual(self.client.get('/api/ui-preferences').json(), DEFAULTS)
        self.assertFalse((self.directory / 'ui-preferences.json').exists())
        response = self.client.put('/api/ui-preferences', json={'locale': 'en'})
        self.assertEqual(response.status_code, 200)
        with TestClient(main.app) as restarted:
            self.assertEqual(restarted.get('/api/ui-preferences').json(), {**DEFAULTS, 'locale': 'en'})
        self.assertEqual(settings.read_bytes(), original)
        self.assertFalse((self.directory / 'papers').exists())

    def test_invalid_language_and_unrelated_settings_are_rejected(self):
        for body in ({'locale': 'fr'}, {'locale': 'en', 'api_key': 'unrelated'}, {'locale': None}):
            self.assertEqual(self.client.put('/api/ui-preferences', json=body).status_code, 422)
        self.assertFalse((self.directory / 'ui-preferences.json').exists())

    def test_legacy_locale_file_gains_read_defaults_without_rewrite_and_old_client_put_keeps_appearance(self):
        target = self.directory / 'ui-preferences.json'
        legacy = b'{"locale":"en"}'
        target.write_bytes(legacy)
        self.assertEqual(self.client.get('/api/ui-preferences').json(), {**DEFAULTS, 'locale': 'en'})
        self.assertEqual(target.read_bytes(), legacy)
        patch = {'scheme': 'dark', 'accent': 'violet', 'ui_font_size': 18, 'note_font_size': 24}
        response = self.client.patch('/api/ui-preferences', json=patch)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'locale': 'en', **patch})
        response = self.client.put('/api/ui-preferences', json={'locale': 'zh-CN'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'locale': 'zh-CN', **patch})
        with TestClient(main.app) as restarted:
            self.assertEqual(restarted.get('/api/ui-preferences').json(), {'locale': 'zh-CN', **patch})

    def test_partial_patch_updates_only_provided_fields_and_preserves_future_disk_properties(self):
        target = self.directory / 'ui-preferences.json'
        stored = {**DEFAULTS, 'locale': 'en', 'scheme': 'dark', 'note_font_size': 20,
                  'future_preference': {'untouched': True}}
        target.write_text(json.dumps(stored), 'utf-8')
        response = self.client.patch('/api/ui-preferences', json={'accent': 'teal', 'ui_font_size': 12})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {key: value for key, value in {**stored, 'accent': 'teal', 'ui_font_size': 12}.items()
                                          if key != 'future_preference'})
        self.assertEqual(json.loads(target.read_text('utf-8')), {**stored, 'accent': 'teal', 'ui_font_size': 12})
        before = target.read_bytes()
        self.assertEqual(self.client.patch('/api/ui-preferences', json={}).status_code, 200)
        self.assertEqual(target.read_bytes(), before)

    def test_invalid_appearance_values_are_rejected_before_any_file_changes(self):
        target = self.directory / 'ui-preferences.json'
        self.client.patch('/api/ui-preferences', json={'scheme': 'light'})
        before = target.read_bytes()
        invalid = [{'scheme': 'sepia'}, {'accent': 'red'}, {'locale': None}, {'scheme': None},
                   {'ui_font_size': None}, {'note_font_size': None}, {'accent': None},
                   {'ui_font_size': 11}, {'ui_font_size': 19}, {'note_font_size': 11}, {'note_font_size': 25},
                   {'ui_font_size': 13.0}, {'note_font_size': 14.5}, {'ui_font_size': True},
                   {'note_font_size': '14'}, {'scheme': 'dark', 'api_key': 'unrelated'},
                   {'theme': '<style>untrusted</style>'}]
        for body in invalid:
            with self.subTest(body=body):
                response = self.client.patch('/api/ui-preferences', json=body)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(target.read_bytes(), before)

    def test_corrupt_preferences_are_preserved(self):
        target = self.directory / 'ui-preferences.json'
        for content in (b'not-json', b'[]', b'{"locale":"invalid"}', b'{}',
                        b'{"locale":"en","scheme":"unexpected"}', b'{"locale":"en","ui_font_size":13.5}',
                        b'{"locale":"en","note_font_size":true}', b'{"locale":"en","accent":null}'):
            target.write_bytes(content)
            self.assertEqual(self.client.get('/api/ui-preferences').status_code, 409)
            self.assertEqual(self.client.put('/api/ui-preferences', json={'locale': 'en'}).status_code, 409)
            self.assertEqual(self.client.patch('/api/ui-preferences', json={'scheme': 'dark'}).status_code, 409)
            self.assertEqual(target.read_bytes(), content)

    def test_failed_atomic_replace_keeps_the_saved_language(self):
        self.client.patch('/api/ui-preferences', json={'locale': 'zh-CN', 'scheme': 'dark', 'accent': 'teal', 'note_font_size': 20})
        before = (self.directory / 'ui-preferences.json').read_bytes()
        import reading_workflow
        with patch.object(reading_workflow.os, 'replace', side_effect=OSError('synthetic disk error')):
            for method, body in [('put', {'locale': 'en'}), ('patch', {'ui_font_size': 18, 'scheme': 'light'})]:
                response = getattr(self.client, method)('/api/ui-preferences', json=body)
                self.assertEqual(response.status_code, 503, response.text)
                self.assertNotIn('synthetic disk error', response.text)
        self.assertEqual((self.directory / 'ui-preferences.json').read_bytes(), before)
        self.assertEqual(self.client.get('/api/ui-preferences').json(), {**DEFAULTS, 'scheme': 'dark', 'accent': 'teal', 'note_font_size': 20})
        self.assertEqual([p.name for p in self.directory.iterdir()], ['ui-preferences.json'])

    def test_concurrent_locale_and_appearance_writes_merge_latest_fields(self):
        entered, release, locale_started = threading.Event(), threading.Event(), threading.Event()
        original_atomic = main.ui.atomic_json

        def blocked_atomic(path, value):
            if value['scheme'] == 'dark' and value['locale'] == 'zh-CN':
                entered.set()
                if not release.wait(2):
                    raise AssertionError('The first preference write was not released')
            original_atomic(path, value)

        def write_locale():
            locale_started.set()
            with TestClient(main.app) as client:
                return client.put('/api/ui-preferences', json={'locale': 'en'})

        with ThreadPoolExecutor(max_workers=2) as executor, patch.object(main.ui, 'atomic_json', blocked_atomic):
            appearance = executor.submit(self.client.patch, '/api/ui-preferences', json={'scheme': 'dark', 'accent': 'violet'})
            try:
                self.assertTrue(entered.wait(2))
                locale = executor.submit(write_locale)
                self.assertTrue(locale_started.wait(2))
                self.assertFalse(locale.done())
            finally:
                release.set()
            self.assertEqual(appearance.result(timeout=2).status_code, 200)
            self.assertEqual(locale.result(timeout=2).status_code, 200)
        self.assertEqual(self.client.get('/api/ui-preferences').json(),
                         {**DEFAULTS, 'locale': 'en', 'scheme': 'dark', 'accent': 'violet'})

    def test_preferences_remain_independent_of_the_paper_storage_lock_and_research_files(self):
        paper = self.directory / 'papers' / ('a' * 32)
        paper.mkdir(parents=True)
        note = paper / 'meta.json'
        note.write_bytes(b'{"notes":"Synthetic unsaved research must not be touched"}')
        settings = self.directory / 'settings.json'
        settings.write_bytes(b'{"model":"synthetic-only"}')
        before = {path: path.read_bytes() for path in (note, settings)}
        with ThreadPoolExecutor(max_workers=1) as executor:
            with main.storage_lock:
                pending = executor.submit(self.client.patch, '/api/ui-preferences', json={'note_font_size': 24})
                self.assertEqual(pending.result(timeout=2).status_code, 200)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)


if __name__ == '__main__':
    unittest.main()
