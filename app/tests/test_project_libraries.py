"""Named project memberships, revision checks and scoped content search."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_documents import main, TestClient


class ProjectLibraryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for attr, value in [('DATA_DIR', self.root), ('PAPERS_DIR', self.root / 'papers'), ('TRASH_DIR', self.root / 'trash')]:
            patcher = patch.object(main, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)
        self.ids = []
        for name in ['First', 'Second']:
            pid, directory = main.new_paper_dir()
            main.write_meta(directory, {**main.blank_meta(pid), 'title': name, 'notes': 'Shared sentence'})
            self.ids.append(pid)

    def create(self, ids):
        response = self.client.post('/api/project-libraries', json={'name': ' Study ', 'paper_ids': ids})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_many_to_many_membership_archive_restore_and_scoped_search(self):
        a, b = self.ids
        first, second = self.create([a]), self.create([a, b])
        path = '/api/project-libraries/' + first['id']
        search = self.client.get('/api/library/search', params={'library_id': first['id'], 'q': 'Shared', 'scope': 'notes'}).json()
        self.assertEqual([row['paper_id'] for row in search['items']], [a])
        update = self.client.put(path + '/papers/' + b)
        self.assertEqual(update.status_code, 200)
        self.assertEqual(self.client.put(path + '/papers/' + b).json(), update.json())
        self.assertEqual(self.client.put(path, json={'revision': first['revision'], 'name': 'Stale'}).status_code, 409)
        archived = self.client.put(path, json={'revision': update.json()['revision'], 'name': 'New', 'archived': True, 'remove': [a]}).json()
        self.assertTrue(archived['archived'])
        self.assertEqual(self.client.put(path + '/papers/' + a).status_code, 409)
        restored = self.client.put(path, json={'revision': archived['revision'], 'name': 'New'}).json()
        self.assertEqual(restored['paper_ids'], [b])
        self.assertEqual(main.project_paper_ids(second['id']), {a, b})
        self.assertEqual(main.project_paper_ids('all'), {a, b})
        self.client.delete('/api/papers/' + b)
        self.assertEqual(main.project_paper_ids(first['id']), set())
        self.assertEqual(self.client.post('/api/trash/' + b + '/restore').status_code, 200)
        self.assertEqual(main.project_paper_ids(first['id']), {b})

    def test_retry_creation_with_same_id(self):
        path = '/api/project-libraries?request_id=' + 'c' * 32
        payload = {'name': 'Retry', 'paper_ids': [self.ids[0]]}
        first = self.client.post(path, json=payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.client.post(path, json=payload).json(), first.json())
        self.assertEqual(len(self.client.get('/api/project-libraries').json()['libraries']), 1)
        self.assertEqual(self.client.post(path, json={**payload, 'name': 'Different'}).status_code, 409)

    def test_corrupt_record_is_never_overwritten_and_members_checked(self):
        self.assertEqual(self.client.post('/api/project-libraries', json={'name': '  '}).status_code, 422)
        self.assertEqual(self.client.post('/api/project-libraries', json={'name': 'X', 'paper_ids': ['a' * 32]}).status_code, 404)
        record = self.create([])
        path = self.root / 'project-libraries' / (record['id'] + '.json')
        other = self.create([])
        path.write_bytes(b'{"version": 2}')
        listing = self.client.get('/api/project-libraries').json()
        self.assertEqual([item['id'] for item in listing['libraries']], [other['id']])
        self.assertEqual(listing['unavailable'], 1)
        self.assertEqual(self.client.put('/api/project-libraries/' + record['id'] + '/papers/' + self.ids[0]).status_code, 409)
        self.assertEqual(path.read_bytes(), b'{"version": 2}')


if __name__ == '__main__':
    unittest.main()
