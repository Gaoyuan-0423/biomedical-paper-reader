"""Deleting a paper moves it to trash; restore returns it, purge destroys it, dedup still sees it."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_documents import fixture_pdf, main, TestClient
from test_zotero_import import ATTACHMENT, ITEM


class TrashTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bioreader-trash-')
        root = Path(self.temp.name)
        self.patchers = [patch.object(main, 'DATA_DIR', root),
                         patch.object(main, 'PAPERS_DIR', root / 'papers'),
                         patch.object(main, 'TRASH_DIR', root / 'trash'),
                         patch.object(main, 'SETTINGS_FILE', root / 'settings.json')]
        for patcher in self.patchers:
            patcher.start()
        self.client = TestClient(main.app)
        self.papers = []
        for name in ('a.pdf', 'b.pdf'):
            response = self.client.post('/api/papers', files={'file': (name, fixture_pdf())})
            self.assertEqual(response.status_code, 200, response.text)
            self.papers.append(response.json())

    def tearDown(self):
        self.client.close()
        for patcher in self.patchers:
            patcher.stop()
        self.temp.cleanup()

    def _delete(self, paper):
        return self.client.delete('/api/papers/' + paper['id'])

    def _zotero_preview(self, item, attachments=()):
        storage = main.DATA_DIR / 'zotero-storage'
        self.client.put('/api/zotero/settings', json={'endpoint': 'http://127.0.0.1:8000/mcp',
                                                     'storage_root': str(storage)})
        for attachment in attachments:
            directory = storage / attachment['key']
            directory.mkdir(parents=True, exist_ok=True)
            (directory / attachment['filename']).write_bytes(fixture_pdf())
        config = main.zi.read_config(main.DATA_DIR)
        return main.zi.preview(main.DATA_DIR, config, item, list(attachments))

    def test_delete_moves_to_trash_and_library_hides_it(self):
        paper = self.papers[0]
        result = self._delete(paper)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertFalse((main.PAPERS_DIR / paper['id']).exists())
        trashed = json.loads((main.TRASH_DIR / paper['id'] / 'meta.json').read_text(encoding='utf-8'))
        self.assertTrue(trashed['trashed_at'])
        listing = self.client.get('/api/papers').json()
        self.assertEqual([p['id'] for p in listing], [self.papers[1]['id']])
        trash = self.client.get('/api/trash').json()
        self.assertEqual([p['id'] for p in trash], [paper['id']])
        self.assertEqual(trash[0]['title'], paper['title'])
        self.assertIn('trashed_at', trash[0])
        self.assertEqual(self.client.get('/api/papers/' + paper['id']).status_code, 404)

    def test_restore_returns_paper_and_clears_trashed_at(self):
        paper = self.papers[0]
        self._delete(paper)
        result = self.client.post('/api/trash/' + paper['id'] + '/restore')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['paper']['id'], paper['id'])
        listing = self.client.get('/api/papers').json()
        # Both papers share the same added_at second; only the set is guaranteed.
        self.assertEqual(sorted(p['id'] for p in listing), sorted([paper['id'], self.papers[1]['id']]))
        raw = json.loads((main.PAPERS_DIR / paper['id'] / 'meta.json').read_text(encoding='utf-8'))
        self.assertNotIn('trashed_at', raw)
        self.assertEqual(self.client.get('/api/papers/' + paper['id']).status_code, 200)
        self.assertEqual(self.client.get('/api/trash').json(), [])

    def test_restore_conflicts_when_library_already_uses_the_id(self):
        paper = self.papers[0]
        self._delete(paper)
        (main.PAPERS_DIR / paper['id']).mkdir(parents=True)
        result = self.client.post('/api/trash/' + paper['id'] + '/restore')
        self.assertEqual(result.status_code, 409, result.text)
        self.assertTrue((main.TRASH_DIR / paper['id']).exists())

    def test_delete_conflicts_without_changing_either_existing_folder(self):
        paper = self.papers[0]
        source = main.PAPERS_DIR / paper['id']
        target = main.TRASH_DIR / paper['id']
        target.mkdir(parents=True)
        (target / 'meta.json').write_text(json.dumps({**paper, 'notes': 'Earlier trashed note'}), encoding='utf-8')
        (target / 'paper.pdf').write_bytes(b'earlier trashed PDF')
        before_source = {p.relative_to(source): p.read_bytes() for p in source.rglob('*') if p.is_file()}
        before_target = {p.relative_to(target): p.read_bytes() for p in target.rglob('*') if p.is_file()}
        result = self._delete(paper)
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual({p.relative_to(source): p.read_bytes() for p in source.rglob('*') if p.is_file()}, before_source)
        self.assertEqual({p.relative_to(target): p.read_bytes() for p in target.rglob('*') if p.is_file()}, before_target)
        self.assertEqual(list(main.TRASH_DIR.iterdir()), [target])

    def test_restore_refuses_future_schema_without_moving_or_rewriting_it(self):
        paper = self.papers[0]
        self.assertEqual(self._delete(paper).status_code, 200)
        source = main.TRASH_DIR / paper['id']
        path = source / 'meta.json'
        raw = json.loads(path.read_text(encoding='utf-8'))
        raw.update(schema_version=main.PAPER_SCHEMA_VERSION + 1, tasks=['Future schema data'])
        path.write_text(json.dumps(raw), encoding='utf-8')
        before = {p.relative_to(source): p.read_bytes() for p in source.rglob('*') if p.is_file()}
        result = self.client.post('/api/trash/' + paper['id'] + '/restore')
        self.assertEqual(result.status_code, 409, result.text)
        self.assertTrue(source.is_dir())
        self.assertFalse((main.PAPERS_DIR / paper['id']).exists())
        self.assertEqual({p.relative_to(source): p.read_bytes() for p in source.rglob('*') if p.is_file()}, before)

    def test_purge_removes_the_folder_for_good(self):
        paper = self.papers[0]
        self._delete(paper)
        result = self.client.delete('/api/trash/' + paper['id'])
        self.assertEqual(result.status_code, 200, result.text)
        self.assertFalse((main.TRASH_DIR / paper['id']).exists())
        self.assertEqual(self.client.get('/api/trash').json(), [])
        self.assertEqual(self.client.delete('/api/trash/' + paper['id']).status_code, 404)

    def test_unknown_trash_ids_are_not_found(self):
        self.assertEqual(self.client.post('/api/trash/' + '0' * 32 + '/restore').status_code, 404)
        self.assertEqual(self.client.delete('/api/trash/' + '0' * 32).status_code, 404)
        self.assertEqual(self.client.post('/api/trash/not-an-id/restore').status_code, 404)

    def test_non_object_trash_metadata_does_not_break_listing_or_imports(self):
        bad = main.TRASH_DIR / ('e' * 32)
        bad.mkdir(parents=True)
        preview = self._zotero_preview(ITEM)
        for index, raw in enumerate(([], None, 'broken', 42)):
            with self.subTest(raw=raw):
                (bad / 'meta.json').write_text(json.dumps(raw), encoding='utf-8')
                listing = self.client.get('/api/trash')
                self.assertEqual(listing.status_code, 200, listing.text)
                self.assertEqual(listing.json(), [])
                record = {'doi': f'10.1000/trash-corrupt-{index}', 'title': 'Synthetic import', 'authors': 'A',
                          'year': 2026, 'journal': 'J', 'abstract': 'Abs', 'url': 'https://example.org'}
                with patch.object(main, 'fetch_crossref', AsyncMock(return_value=record)):
                    imported = self.client.post('/api/import/doi', json={'doi': record['doi']})
                self.assertEqual(imported.status_code, 200, imported.text)
                zotero = self.client.post('/api/zotero/import', json={'preview_id': preview})
                self.assertEqual(zotero.status_code, 200, zotero.text)

    def test_zotero_import_is_blocked_by_trashed_duplicate(self):
        preview = self._zotero_preview(ITEM)
        first = self.client.post('/api/zotero/import', json={'preview_id': preview})
        self.assertEqual(first.status_code, 200, first.text)
        paper = first.json()['paper']
        self.assertEqual(self._delete(paper).status_code, 200)
        path = main.TRASH_DIR / paper['id'] / 'meta.json'
        before = path.read_bytes()
        result = self.client.post('/api/zotero/import', json={'preview_id': preview})
        self.assertEqual(result.status_code, 409, result.text)
        self.assertIn('请先恢复或彻底删除后再导入', result.json()['detail'])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual({p['id'] for p in main.list_meta()}, {p['id'] for p in self.papers})

    def test_zotero_checks_trash_again_after_pdf_parsing(self):
        paper = self.papers[0]
        directory = main.PAPERS_DIR / paper['id']
        meta = main.read_meta(directory)
        meta['doi'] = ITEM['doi']
        main.write_meta(directory, meta)
        item = dict(ITEM, key='OTHER123', item_key='OTHER123', doi='')
        attachment = dict(ATTACHMENT, parent_key='OTHER123')
        preview = self._zotero_preview(item, [attachment])
        original = main.ingest_pdf
        def parse_with_doi(*args):
            parsed = original(*args)
            parsed['doi'] = ITEM['doi']
            main.delete_paper(paper['id'])
            return parsed
        with patch.object(main, 'ingest_pdf', parse_with_doi):
            result = self.client.post('/api/zotero/import', json={'preview_id': preview, 'attachment_key': attachment['key']})
        self.assertEqual(result.status_code, 409, result.text)
        self.assertIn('请先恢复或彻底删除后再导入', result.json()['detail'])
        self.assertTrue((main.TRASH_DIR / paper['id']).is_dir())
        self.assertEqual([p['id'] for p in main.list_meta()], [self.papers[1]['id']])
        self.assertEqual(list(main.DATA_DIR.glob('.zotero-import-*')), [])

    def test_doi_import_is_blocked_by_trashed_duplicate_until_restored_or_purged(self):
        record = {'doi': '10.1000/trash-dedup', 'title': '回收站查重', 'authors': 'A',
                  'year': 2026, 'journal': 'J', 'abstract': 'Abs', 'url': 'https://example.org'}
        with patch.object(main, 'fetch_crossref', AsyncMock(return_value=record)):
            first = self.client.post('/api/import/doi', json={'doi': record['doi']})
            self.assertEqual(first.status_code, 200, first.text)
            imported = first.json()
            self.assertEqual(self._delete(imported).status_code, 200)
            blocked = self.client.post('/api/import/doi', json={'doi': record['doi']})
            self.assertEqual(blocked.status_code, 409, blocked.text)
            self.assertIn('回收站', blocked.json()['detail'])
            self.assertEqual(self.client.post('/api/trash/' + imported['id'] + '/restore').status_code, 200)
            again = self.client.post('/api/import/doi', json={'doi': record['doi']})
            self.assertEqual(again.status_code, 409, again.text)
            self.assertIn('文献库里已有', again.json()['detail'])
            self.assertEqual(self.client.delete('/api/trash/' + imported['id']).status_code, 404)


if __name__ == '__main__':
    unittest.main()
