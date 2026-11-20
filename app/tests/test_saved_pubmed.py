import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_documents import main, TestClient


class SavedPubmedTests(unittest.TestCase):
    def test_refresh_marks_only_reviewed_snapshot_and_scopes_by_project(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(main, 'DATA_DIR', Path(tmp)):
            c = TestClient(main.app)
            project = c.post('/api/project-libraries', json={'name':'Example'}).json()['id']
            base = '/api/pubmed-searches/' + project
            record = c.post(base,json={'query':' retinopathy[Title] '}).json()
            self.assertEqual(record['id'],c.post(base,json={'query':'retinopathy[Title]'}).json()['id'])
            path = base + '/' + record['id']
            self.assertEqual(c.get('/api/pubmed-searches/all').json(), [])
            with patch.object(main.sp,'search', AsyncMock(side_effect=[(120,['311','312']), (121,['313','312'])])):
                first = c.post(path+'/refresh').json()
                self.assertEqual(first['unseen'],['311','312'])
                self.assertEqual(c.post(path+'/seen', json={'result_id':first['result_id']}).json()['unseen'], [])
                second = c.post(path+'/refresh').json()
                self.assertEqual(second['unseen'], ['313'])
                self.assertEqual(c.post(path+'/seen', json={'result_id':first['result_id']}).status_code,409)
                self.assertEqual(c.get(base).json()[0]['unseen'], ['313'])


if __name__ == '__main__':
    unittest.main()
