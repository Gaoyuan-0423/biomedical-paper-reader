"""Bulk actions freeze their scope and own only the labels they actually add."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_documents import TestClient, main


class BulkTagTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for key, value in {'DATA_DIR':root, 'PAPERS_DIR':root/'papers', 'TRASH_DIR':root/'trash'}.items():
            self.enterContext(patch.object(main, key, value))
        self.client = self.enterContext(TestClient(main.app))
        self.ids = [str(i) * 32 for i in range(1, 6)]
        for pid in self.ids:
            directory = root / 'papers' / pid
            directory.mkdir(parents=True)
            main.write_meta(directory, main.blank_meta(pid))
        self.url = '/api/library/all/tag-batch'

    def tags(self, index, values=None):
        url = '/api/papers/' + self.ids[index]
        response = self.client.get(url) if values is None else self.client.patch(url, json={'tags':values})
        self.assertEqual(response.status_code, 200)
        return response.json()['tags']

    def batch(self, **changes):
        body = dict(request_id='a'*32, paper_ids=self.ids, tag='待核对')
        return self.client.post(self.url, json={**body, **changes})

    def undo(self):
        response = self.client.post(self.url + '/' + 'a' * 32 + '/undo')
        self.assertEqual(response.status_code, 200, response.text)

    def test_undo_keeps_existing_unrelated_and_removed_readded_labels(self):
        self.tags(0, ['待核对'])
        self.tags(1, ['original'])
        self.tags(3, [str(i) for i in range(30)])
        saved = self.batch().json()
        self.assertEqual((saved['added'], saved['existing'], saved['skipped']), (3,1,1))
        self.assertEqual(self.batch().json(), saved)
        self.assertEqual(self.batch(tag='不相同').status_code, 409)
        self.tags(1, ['original','待核对','later'])
        self.tags(2, [])
        self.tags(2, ['待核对'])
        self.undo()
        self.undo()
        self.assertEqual(self.tags(0), ['待核对'])
        self.assertEqual(self.tags(1), ['original','later'])
        self.assertEqual(self.tags(2), ['待核对'])
        self.assertEqual(self.tags(4), [])
        self.assertEqual(len(self.tags(3)), 30)

    def test_scoped_snapshot_conflicts_and_noop_keeps_last_undo(self):
        pid = self.client.post('/api/project-libraries', json={'name':'project','paper_ids':self.ids[:2]}).json()['id']
        self.url = f'/api/library/{pid}/tag-batch'
        self.assertEqual(self.batch().status_code, 409)
        self.assertEqual(self.tags(0), [])
        result = self.batch(paper_ids=self.ids[:2]).json()
        self.assertEqual(result['added'], 2)
        self.assertEqual(self.batch(request_id='b'*32, paper_ids=self.ids[:1]).status_code, 409)
        noop = self.batch(request_id='b'*32, previous_id='a'*32, paper_ids=self.ids[:1]).json()
        self.assertEqual(noop['receipt'], result['receipt'])
        self.assertEqual(noop['added'], 0)
        self.undo()
        self.assertEqual(self.tags(0), [])
        self.assertEqual(self.tags(1), [])

    def test_interrupted_write_replays_without_duplicating_and_undo_survives_trash(self):
        original = main.write_meta
        calls = 0
        def once(directory, meta):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('temporary IO error')
            original(directory, meta)
        with patch.object(main, 'write_meta', side_effect=once), self.assertRaises(OSError):
            self.batch()
        restored = self.client.get(self.url).json()
        self.assertEqual(restored['count'], 5)
        main.TRASH_DIR.mkdir()
        (main.PAPERS_DIR / self.ids[2]).rename(main.TRASH_DIR / self.ids[2])
        self.undo()
        self.assertEqual(main.read_meta(main.TRASH_DIR / self.ids[2])['tags'], [])
        self.assertEqual(self.tags(0), [])

    def test_stale_single_edit_cannot_remove_a_new_batch_tag(self):
        self.batch()
        updated = self.client.patch('/api/papers/' + self.ids[0], json={'tags':['manual'], 'tags_revision':0})
        self.assertEqual(updated.status_code, 409)
        self.assertEqual(self.tags(0), ['待核对'])
