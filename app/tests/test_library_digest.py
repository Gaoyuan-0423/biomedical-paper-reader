"""Catalog the existing storage without changing memberships or reading artifacts."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from test_documents import TestClient, fixture_pdf, main
from library_digest import figure_image_path
from reading_workflow import atomic_json


class LibraryDigestTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.papers = root / 'papers'
        self.papers.mkdir()
        for attr, path in [('DATA_DIR', root), ('PAPERS_DIR', self.papers), ('TRASH_DIR', root / 'trash')]:
            self.enterContext(patch.object(main, attr, path))
        self.client = self.enterContext(TestClient(main.app))
        self.a, self.b, self.c = ['%032x' % i for i in range(1, 4)]
        for pid, tag in ((self.a, 'CAF'), (self.b, 'caf'), (self.c, '')):
            directory = self.papers / pid
            directory.mkdir()
            meta = dict(id=pid, title='One article' if pid != self.c else 'Collection record', doi='10.0/shared',
                        authors='Lin Xu; Wu Li', year=2025, tags=[tag] if tag else [],
                        notes='## 核心结论\n\nA saved finding.' if pid == self.b else '',
                        pages=1, document_revisions={'main': 'rev'}, notes_revision=3, material_revision=2)
            atomic_json(directory / 'meta.json', meta)
            if pid != self.c:
                (directory / 'paper.pdf').write_bytes(fixture_pdf(marker=pid))
                self.figure(directory, pid)
        self.first = self.project('First', [self.a, self.b])
        self.second = self.project('Second', [self.b, self.c])

    @staticmethod
    def figure(directory, label):
        (directory / 'figures').mkdir()
        atomic_json(directory / 'figures.json', [dict(id='f001', page=1, label='Fig 1', tags=['panel'],
                    caption=label, observation='Stored observation')])
        Image.new('RGB', (12, 12), '#806020').save(directory / 'figures' / 'f001.png')

    def project(self, name, papers):
        result = self.client.post('/api/project-libraries', json=dict(name=name, paper_ids=papers))
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()['id']

    def digest(self, scope):
        response = self.client.get(f'/api/library/{scope}/digest')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_existing_scopes_overlap_and_same_figure_names(self):
        all_papers = self.digest('all')
        self.assertEqual(all_papers['counts']['papers'], 3)
        self.assertEqual(all_papers['counts']['with_notes'], 1)
        self.assertEqual(all_papers['counts']['with_pdf'], 2)
        self.assertEqual(all_papers['counts']['unclassified'], 1)
        self.assertEqual(all_papers['topics'], [dict(name='CAF', count=2)])
        first, second = self.digest(self.first), self.digest(self.second)
        self.assertEqual({r['id'] for r in first['papers']}, {self.a, self.b})
        self.assertEqual({r['id'] for r in second['papers']}, {self.b, self.c})
        shared = next(p for p in first['papers'] if p['id'] == self.b)
        self.assertEqual({lib['name'] for lib in shared['libraries']}, {'First', 'Second'})
        self.assertEqual(shared['authors'], ['Lin Xu', 'Wu Li'])
        self.assertEqual(shared['notes_revision'], 3)
        self.assertEqual({f['label'] for f in first['figures']}, {'Fig 1'})
        self.assertEqual({f['paper_id'] for f in second['figures']}, {self.b})
        for fig in first['figures']:
            self.assertEqual(self.client.get(fig['image_url']).content, figure_image_path(self.papers, fig).read_bytes())
            self.assertEqual(self.client.get(fig['source_url']).json()['paper_id'], fig['paper_id'])
        excluded = next(f for f in first['figures'] if f['paper_id'] == self.a)
        self.assertEqual(self.client.get(excluded['source_url'].replace(self.first, self.second)).status_code, 404)

    def test_changed_document_or_image_cannot_reuse_old_source(self):
        fig = next(f for f in self.digest(self.first)['figures'] if f['paper_id'] == self.a)
        directory = self.papers / self.a
        (directory / 'figures' / 'f001.png').write_bytes(b're-extracted')
        self.assertEqual(self.client.get(fig['image_url']).status_code, 409)
        self.assertIsNone(figure_image_path(self.papers, fig))
        updated = next(f for f in self.digest(self.first)['figures'] if f['paper_id'] == self.a)
        path = directory / 'meta.json'
        meta = json.loads(path.read_text())
        meta['document_revisions']['main'] = 'rev2'
        atomic_json(path, meta)
        self.assertEqual(self.client.get(updated['source_url']).status_code, 409)

    def test_partial_library_and_damaged_or_stale_materials(self):
        directory = self.papers / self.b
        fig_path = directory / 'figures.json'
        records = json.loads(fig_path.read_text())
        records[0]['observation_source_revision'] = 'old'
        atomic_json(fig_path, records)
        row, = self.digest(self.second)['figures']
        self.assertFalse(row['observation'])
        self.assertTrue(row['observation_stale'])
        fig_path.write_text('corrupt')
        (self.papers / self.c / 'meta.json').write_text('[]')
        subset = self.digest(self.second)
        self.assertEqual([item['id'] for item in subset['papers']], [self.b])
        self.assertEqual(subset['counts']['unavailable'], 1)
        self.assertEqual(subset['counts']['damaged_figure_records'], 1)
        self.assertEqual(fig_path.read_text(), 'corrupt')
        self.assertEqual(self.client.get('/api/library/missing/digest').status_code, 404)

    def test_recorded_pdf_digest_is_checked_on_access(self):
        directory = self.papers / self.a
        path = directory / 'meta.json'
        meta = json.loads(path.read_text())
        meta['document_revisions']['main'] = hashlib.sha256((directory / 'paper.pdf').read_bytes()).hexdigest()
        atomic_json(path, meta)
        fig = next(f for f in self.digest(self.first)['figures'] if f['paper_id'] == self.a)
        self.assertEqual(self.client.get(fig['source_url']).status_code, 200)
        (directory / 'paper.pdf').write_bytes(fixture_pdf(marker='replaced outside app'))
        refreshed = next(f for f in self.digest(self.first)['figures'] if f['paper_id'] == self.a)
        self.assertEqual(self.client.get(refreshed['image_url']).status_code, 409)
        self.assertIsNone(figure_image_path(self.papers, refreshed))


if __name__ == '__main__':
    unittest.main()
