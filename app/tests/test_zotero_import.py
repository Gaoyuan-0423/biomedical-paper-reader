"""Zotero import preserves existing papers and consumes only selected managed PDFs."""
import asyncio
import copy
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from test_documents import fixture_pdf, main, TestClient
import zotero_import as zi

ITEM = {'key': 'PAPER123', 'item_key': 'PAPER123', 'item_type': 'journalArticle',
        'title': 'Synthetic Zotero study', 'authors': 'Test Author', 'year': '2026',
        'doi': '10.1234/zotero-test', 'journal': 'Synthetic Journal', 'abstract': 'Synthetic abstract',
        'url': 'https://example.org/paper', 'tags': ['Zotero tag'], 'library_type': 'user', 'library_id': '42'}
ATTACHMENT = {'key': 'ATTACH12', 'parent_key': ITEM['key'], 'title': 'Full Text',
              'filename': 'paper.pdf', 'content_type': 'application/pdf', 'link_mode': 'imported_file',
              'library_type': 'user', 'library_id': '42'}


class FakeMCP:
    instances = []
    item = ITEM
    attachments = [ATTACHMENT]
    def __init__(self, endpoint, bearer=''):
        self.endpoint, self.bearer = endpoint, bearer
        self.instances.append(self)
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def discover(self):
        return {'server_name': 'Synthetic MCP', 'protocol_version': '2025-06-18',
                'capabilities': {'search': True, 'metadata': True, 'attachments': True}}
    async def search(self, query, limit=20, start=0):
        return {'items': [copy.deepcopy(self.item)], 'has_more': False}
    async def get_item(self, key): return copy.deepcopy(self.item)
    async def get_attachments(self, key, parent=None): return copy.deepcopy(self.attachments)


class ZoteroImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bioreader-zotero-')
        self.directory = Path(self.temp.name)
        self.storage = self.directory / 'zotero-storage'
        (self.storage / ATTACHMENT['key']).mkdir(parents=True)
        self.pdf = fixture_pdf(marker='ZOTERO')
        (self.storage / ATTACHMENT['key'] / 'paper.pdf').write_bytes(self.pdf)
        self.patches = [patch.object(main, 'DATA_DIR', self.directory),
                        patch.object(main, 'PAPERS_DIR', self.directory / 'papers'),
                        patch.object(main, 'SETTINGS_FILE', self.directory / 'settings.json'),
                        patch.object(main, 'ZoteroMCPClient', FakeMCP)]
        for p in self.patches: p.start()
        FakeMCP.instances = []; FakeMCP.item = copy.deepcopy(ITEM); FakeMCP.attachments = [copy.deepcopy(ATTACHMENT)]
        self.client = TestClient(main.app)
        self.config = self.client.put('/api/zotero/settings', json={
            'endpoint': 'http://127.0.0.1:8000/mcp', 'storage_root': str(self.storage), 'token': 'synthetic-token'}).json()

    def tearDown(self):
        self.client.close()
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def preview(self):
        response = self.client.get('/api/zotero/items/' + ITEM['key'],
                                   params={'configuration_id': self.config['configuration_id']})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def do_import(self, preview, key=''):
        return self.client.post('/api/zotero/import', json={'preview_id': preview['preview_id'], 'attachment_key': key})

    def test_settings_and_unsaved_connection_check_never_expose_or_forward_old_token(self):
        self.assertNotIn('synthetic-token', json.dumps(self.config))
        self.assertTrue(self.config['has_token'])
        before = (self.directory / 'zotero.json').read_bytes()
        result = self.client.post('/api/zotero/check', json={'endpoint': 'https://mcp.example.org/mcp', 'token': ''})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(FakeMCP.instances[-1].bearer, '')
        self.assertEqual((self.directory / 'zotero.json').read_bytes(), before)
        self.client.put('/api/zotero/settings', json={'token': ''})
        self.assertTrue(self.client.get('/api/zotero/settings').json()['has_token'])
        self.client.put('/api/zotero/settings', json={'clear_token': True})
        self.assertFalse(self.client.get('/api/zotero/settings').json()['has_token'])
        self.assertFalse((self.directory / 'settings.json').exists())

    def test_default_storage_uses_current_users_home_not_the_build_machine(self):
        with patch.object(zi.Path, 'home', return_value=Path('/Users/another-reader')):
            config = zi.read_config(self.directory / 'new-install')
        self.assertEqual(config['storage_root'], '/Users/another-reader/Zotero/storage')
        self.assertEqual(config['endpoint'], 'http://127.0.0.1:8000/mcp')
        self.assertEqual(config['token'], '')

    def test_corrupt_configuration_and_failed_setting_write_preserve_original(self):
        path = self.directory / 'zotero.json'; before = path.read_bytes()
        with patch.object(zi, 'atomic_json', side_effect=OSError('disk private detail')):
            result = self.client.put('/api/zotero/settings', json={'clear_token': True})
        self.assertEqual(result.status_code, 503)
        self.assertEqual(path.read_bytes(), before)
        path.write_bytes(b'{broken')
        self.assertEqual(self.client.put('/api/zotero/settings', json={'clear_token': True}).status_code, 409)
        self.assertEqual(path.read_bytes(), b'{broken')

    def test_search_and_old_configuration_preview_are_isolated(self):
        result = self.client.post('/api/zotero/search', json={'query': '  study  '})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['items'][0]['key'], ITEM['key'])
        self.assertNotIn('synthetic-token', result.text)
        self.client.put('/api/zotero/settings', json={'clear_token': True})
        result = self.client.get('/api/zotero/items/' + ITEM['key'], params={'configuration_id': self.config['configuration_id']})
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.client.post('/api/zotero/search', json={'query': '  '}).status_code, 422)

    def test_late_search_does_not_cross_a_changed_connection(self):
        async def late(*args, **kwargs):
            config = zi.merge_config(zi.read_config(self.directory), zi.ZoteroConfigIn(clear_token=True))
            zi.write_config(self.directory, config)
            return {'items': [ITEM]}
        with patch.object(FakeMCP, 'search', late):
            result = self.client.post('/api/zotero/search', json={'query': 'study'})
        self.assertEqual(result.status_code, 409)

    def test_metadata_only_import_has_no_pdf_and_keeps_zotero_fields(self):
        preview = self.preview()
        self.assertTrue(preview['attachments'][0]['available'])
        result = self.do_import(preview)
        self.assertEqual(result.status_code, 200, result.text)
        paper = result.json()['paper']
        self.assertFalse(paper['has_file']); self.assertFalse(result.json()['existing'])
        self.assertEqual(paper['title'], ITEM['title']); self.assertEqual(paper['tags'], ITEM['tags'])
        self.assertEqual(paper['zotero_source']['library_id'], '42')
        self.assertFalse((main.paper_dir(paper['id']) / 'paper.pdf').exists())
        self.assertNotIn('synthetic-token', json.dumps(paper))
        self.assertNotIn(str(self.storage), json.dumps(paper))

    def test_selected_pdf_import_keeps_bytes_and_is_available_to_reading(self):
        result = self.do_import(self.preview(), ATTACHMENT['key'])
        self.assertEqual(result.status_code, 200, result.text)
        paper = result.json()['paper']; directory = main.paper_dir(paper['id'])
        self.assertTrue(paper['has_file']); self.assertEqual(paper['pages'], 1)
        self.assertEqual((directory / 'paper.pdf').read_bytes(), self.pdf)
        self.assertIn('ZOTERO', (directory / 'text.txt').read_text())
        self.assertTrue((directory / 'blocks/main.json').exists())
        self.assertEqual(paper['title'], ITEM['title']); self.assertEqual(paper['notes'], '')
        self.assertEqual(self.client.get('/api/papers/' + paper['id'] + '/file').status_code, 200)

    def test_repeated_source_and_doi_import_never_overwrite_existing_notes(self):
        preview = self.preview(); paper = self.do_import(preview, ATTACHMENT['key']).json()['paper']
        self.client.patch('/api/papers/' + paper['id'], json={'notes': 'Human note', 'title': 'Human title'})
        before = (main.paper_dir(paper['id']) / 'meta.json').read_bytes()
        repeated = self.do_import(preview).json()
        self.assertTrue(repeated['existing']); self.assertEqual(repeated['paper']['id'], paper['id'])
        FakeMCP.item['key'] = FakeMCP.item['item_key'] = 'OTHER123'
        FakeMCP.item['doi'] = 'https://doi.org/10.1234/ZOTERO-TEST'
        pid = zi.preview(self.directory, zi.read_config(self.directory), FakeMCP.item, [])
        result = self.client.post('/api/zotero/import', json={'preview_id': pid})
        self.assertTrue(result.json()['existing'])
        self.assertEqual((main.paper_dir(paper['id']) / 'meta.json').read_bytes(), before)
        self.assertEqual(len(main.list_meta()), 1)

    def test_concurrent_imports_commit_one_paper(self):
        preview = self.preview(); config = zi.read_config(self.directory)
        record = zi.get_preview(self.directory, config, preview['preview_id'])
        def run():
            return main._import_zotero_preview(self.directory, main.PAPERS_DIR, config, record, ATTACHMENT['key'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run(), range(2)))
        self.assertEqual(len({r['paper']['id'] for r in results}), 1)
        self.assertEqual(sum(not r['existing'] for r in results), 1)
        self.assertEqual(len(main.list_meta()), 1)

    def test_doi_found_during_pdf_parsing_is_checked_before_commit(self):
        first = self.do_import(self.preview()).json()['paper']
        self.client.patch('/api/papers/' + first['id'], json={'notes': 'Existing human note'})
        FakeMCP.item.update(key='OTHER123', item_key='OTHER123', doi='')
        attachment = dict(ATTACHMENT, parent_key='OTHER123')
        config = zi.read_config(self.directory)
        pid = zi.preview(self.directory, config, FakeMCP.item, [attachment])
        original = main.ingest_pdf
        def parse_with_doi(*args):
            meta = original(*args); meta['doi'] = ITEM['doi']; return meta
        with patch.object(main, 'ingest_pdf', parse_with_doi):
            result = self.client.post('/api/zotero/import', json={'preview_id': pid, 'attachment_key': ATTACHMENT['key']})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['existing'])
        self.assertEqual(result.json()['paper']['notes'], 'Existing human note')
        self.assertEqual(len(main.list_meta()), 1)

    def test_background_import_and_manual_crop_use_native_pdf_safely(self):
        preview = self.preview(); config = zi.read_config(self.directory)
        record = zi.get_preview(self.directory, config, preview['preview_id'])
        ready = threading.Barrier(2)
        def importing():
            ready.wait()
            return main._import_zotero_preview(self.directory, main.PAPERS_DIR, config, record, ATTACHMENT['key'])
        def cropping():
            ready.wait()
            return main.crop_figure(self.storage / ATTACHMENT['key'] / 'paper.pdf',
                                    self.directory / 'manual-crops', 1, [60, 330, 460, 530], 'f001')
        with ThreadPoolExecutor(max_workers=2) as pool:
            imported, cropped = pool.submit(importing), pool.submit(cropping)
            self.assertTrue(imported.result()['paper']['has_file'])
            self.assertGreater(cropped.result()['width'], 0)
        self.assertTrue((self.directory / 'manual-crops/f001.png').exists())

    def test_missing_linked_foreign_and_escaping_attachments_are_unavailable(self):
        cases = [dict(ATTACHMENT, filename='missing.pdf'), dict(ATTACHMENT, link_mode='linked_file'),
                 dict(ATTACHMENT, parent_key='OTHER123'), dict(ATTACHMENT, library_id='99'),
                 dict(ATTACHMENT, filename='../paper.pdf'), dict(ATTACHMENT, content_type='text/html')]
        for attachment in cases:
            with self.subTest(attachment=attachment):
                FakeMCP.attachments = [attachment]
                preview = self.preview()
                self.assertFalse(preview['attachments'][0]['available'])
                self.assertEqual(self.do_import(preview, ATTACHMENT['key']).status_code, 422)
                self.assertEqual(main.list_meta(), [])
        outside = self.directory / 'outside.pdf'; outside.write_bytes(self.pdf)
        local = self.storage / ATTACHMENT['key'] / 'paper.pdf'; local.unlink(); local.symlink_to(outside)
        FakeMCP.attachments = [ATTACHMENT]
        self.assertFalse(self.preview()['attachments'][0]['available'])

    def test_remote_or_unknown_library_is_metadata_only(self):
        self.config = self.client.put('/api/zotero/settings', json={'endpoint': 'https://mcp.example.org/mcp'}).json()
        preview = self.preview(); self.assertFalse(preview['attachments'][0]['available'])
        self.assertEqual(self.do_import(preview, ATTACHMENT['key']).status_code, 422)
        self.assertEqual(self.do_import(preview).status_code, 200)

    def test_bad_pdf_and_failed_parse_do_not_leave_half_imported_papers(self):
        (self.storage / ATTACHMENT['key'] / 'paper.pdf').write_bytes(b'%PDF-invalid')
        result = self.do_import(self.preview(), ATTACHMENT['key'])
        self.assertEqual(result.status_code, 422, result.text)
        self.assertEqual(main.list_meta(), [])
        self.assertEqual(list(self.directory.glob('.zotero-import-*')), [])

    def test_stale_and_expired_previews_cannot_import(self):
        preview = self.preview()
        with patch.object(zi.time, 'monotonic', return_value=zi.time.monotonic() + 700):
            self.assertEqual(self.do_import(preview).status_code, 409)
        self.client.put('/api/zotero/settings', json={'clear_token': True})
        self.assertEqual(self.do_import(preview).status_code, 409)
        self.assertEqual(self.client.post('/api/zotero/import', json={'preview_id': '0' * 32}).status_code, 409)

    def test_setting_change_while_parsing_and_disk_failure_leave_no_new_paper(self):
        original = main.ingest_pdf
        def changed(*args):
            result = original(*args)
            config = zi.merge_config(zi.read_config(self.directory), zi.ZoteroConfigIn(clear_token=True))
            zi.write_config(self.directory, config)
            return result
        preview = self.preview()
        with patch.object(main, 'ingest_pdf', changed):
            self.assertEqual(self.do_import(preview, ATTACHMENT['key']).status_code, 409)
        self.assertEqual(main.list_meta(), [])
        self.config = self.client.get('/api/zotero/settings').json()
        preview = self.preview()
        with patch.object(main, 'write_meta', side_effect=OSError('private disk error')):
            result = self.do_import(preview)
        self.assertEqual(result.status_code, 503)
        self.assertNotIn('private disk error', result.text)
        self.assertEqual(main.list_meta(), [])


if __name__ == '__main__':
    unittest.main()
