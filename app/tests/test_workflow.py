"""Only actionable gaps become tasks; legacy metadata and source evidence survive."""
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from pathlib import Path
from unittest.mock import patch

from test_documents import fixture_pdf, main, TestClient


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bioreader-workflow-')
        directory = Path(self.temp.name)
        self.patchers = [patch.object(main, 'DATA_DIR', directory),
                         patch.object(main, 'PAPERS_DIR', directory / 'papers'),
                         patch.object(main, 'SETTINGS_FILE', directory / 'settings.json')]
        for p in self.patchers:
            p.start()
        self.client = TestClient(main.app)
        result = self.client.post('/api/papers', files={'file': ('main.pdf', fixture_pdf())})
        self.assertEqual(result.status_code, 200, result.text)
        self.paper = result.json()
        self.url = '/api/papers/' + self.paper['id']
        self.directory = main.paper_dir(self.paper['id'])

    def tearDown(self):
        self.client.close()
        for p in self.patchers:
            p.stop()
        self.temp.cleanup()

    def evidence(self, **fields):
        result = self.client.post(self.url + '/evidence', json={
            'claim': 'This synthetic result supports the stated comparison.',
            'quote': 'Synthetic result from MAIN.', 'page': 1, **fields})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def supplement(self):
        result = self.client.post(self.url + '/supplements', files={
            'file': ('supp.pdf', fixture_pdf('Figure S1', 'SUPPLEMENT'))})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()['supplements'][0]

    def task_ids(self):
        return {t['id'] for t in self.client.get(self.url).json()['tasks']}

    def test_legacy_reading_fields_are_preserved_without_creating_tasks(self):
        meta = json.loads((self.directory / 'meta.json').read_text())
        for key in ('reading_status', 'reading_decision', 'notes_incomplete', 'material_revision', 'notes_material_revision', 'document_revisions'):
            meta.pop(key, None)
        (self.directory / 'meta.json').write_text(json.dumps(meta))
        legacy = self.client.get(self.url).json()
        self.assertEqual(legacy['reading_status'], 'unread')
        self.assertEqual(legacy['reading_decision'], 'pending')
        self.assertEqual(self.task_ids(), set())
        meta.update(reading_status='reading', reading_decision='pending')
        (self.directory / 'meta.json').write_text(json.dumps(meta))
        self.client.patch(self.url, json={'notes': 'New note'})
        listed = self.client.get('/api/papers').json()[0]
        self.assertEqual(listed['reading_status'], 'reading')
        self.assertEqual(listed['reading_decision'], 'pending')
        self.assertNotIn('notes', listed)
        self.assertEqual(self.task_ids(), set())
        saved = json.loads((self.directory / 'meta.json').read_text())
        self.assertEqual(saved['reading_status'], 'reading')
        self.assertEqual(saved['reading_decision'], 'pending')

    def test_legacy_irrelevant_decision_cannot_hide_a_missing_fulltext_task(self):
        pid, directory = main.new_paper_dir()
        main.write_meta(directory, main.blank_meta(pid))
        url = '/api/papers/' + pid
        self.assertEqual({t['id'] for t in self.client.get(url).json()['tasks']}, {'fulltext'})
        meta = main.read_meta(directory)
        meta.update(reading_decision='irrelevant', reading_status='reading')
        main.write_meta(directory, meta)
        result = self.client.get(url).json()
        self.assertEqual({t['id'] for t in result['tasks']}, {'fulltext'})

    def test_manual_edits_do_not_silently_clear_incomplete_or_stale_notes(self):
        first = self.client.patch(self.url, json={'notes': 'Partial notes', 'notes_incomplete': True}).json()
        self.assertFalse(first['notes_stale'])
        self.supplement()
        self.assertIn('notes_stale', self.task_ids())
        result = self.client.patch(self.url, json={'notes': 'A corrected sentence'}).json()
        self.assertTrue(result['notes_stale'])
        self.assertTrue(result['notes_incomplete'])
        result = self.client.patch(self.url, json={'notes_reviewed': True, 'notes_incomplete': False}).json()
        self.assertFalse(result['notes_stale'])
        self.assertFalse(result['notes_incomplete'])
        self.assertNotIn('tasks', json.loads((self.directory / 'meta.json').read_text()))

    def test_concurrent_note_and_favorite_patches_preserve_both_fields(self):
        first_read, second_read, release = Event(), Event(), Event()
        original = main.read_meta
        calls = []

        def delayed_read(directory):
            result = original(directory)
            calls.append(1)
            if len(calls) == 1:
                first_read.set()
                if not release.wait(3):
                    raise RuntimeError('Test did not release first PATCH')
            else:
                second_read.set()
            return result

        with patch.object(main, 'read_meta', delayed_read), ThreadPoolExecutor(max_workers=2) as pool:
            notes = pool.submit(self.client.patch, self.url, json={'notes': 'Concurrent note'})
            self.assertTrue(first_read.wait(3))
            favorite = pool.submit(self.client.patch, self.url, json={'favorite': True})
            try:
                self.assertFalse(second_read.wait(0.1), 'second PATCH read stale metadata before first committed')
            finally:
                release.set()
            self.assertEqual(notes.result(timeout=3).status_code, 200)
            self.assertEqual(favorite.result(timeout=3).status_code, 200)
        meta = self.client.get(self.url).json()
        self.assertEqual(meta['notes'], 'Concurrent note')
        self.assertTrue(meta['favorite'])

    def test_interrupted_generation_records_its_original_material_revision(self):
        revision = self.paper['material_revision']
        self.supplement()
        result = self.client.patch(self.url, json={'notes': 'Partial older-source notes',
                                                  'notes_incomplete': True, 'notes_source_revision': revision}).json()
        self.assertTrue(result['notes_stale'])
        self.assertTrue(result['notes_incomplete'])
        invalid = self.client.patch(self.url, json={'notes': 'Impossible future', 'notes_source_revision': 9999})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(self.client.get(self.url).json()['notes'], 'Partial older-source notes')

    def test_generation_preserves_user_metadata_and_detects_material_change_during_run(self):
        self.client.patch(self.url, json={'notes': 'Old', 'notes_incomplete': True})
        self.client.put('/api/settings', json={'api_key': 'fixture-only', 'model': 'test'})

        async def stream(*args, **kwargs):
            current = main.read_meta(self.directory)
            current['favorite'] = True
            current['tags'] = ['manual tag']
            main.materials_changed(current, 'new-document', 'changed-during-generation')
            main.write_meta(self.directory, current)
            yield 'Generated notes'

        with patch.object(main, 'stream_chat', stream):
            result = self.client.post(self.url + '/read?use_figures=0')
        events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith('data: ')]
        done = next(e for e in events if e['type'] == 'done')
        self.assertTrue(done['paper']['notes_stale'])
        meta = self.client.get(self.url).json()
        self.assertTrue(meta['favorite'])
        self.assertEqual(meta['tags'], ['manual tag'])
        self.assertFalse(meta['notes_incomplete'])
        self.assertTrue(meta['notes_stale'])

    def test_evidence_roundtrip_validation_and_tasks(self):
        fig = self.client.get(self.url + '/figures').json()[0]
        record = self.evidence(figure_id=fig['id'])
        self.assertTrue(record['source_available'])
        self.assertFalse(record['source_changed'])
        self.assertIn('evidence', self.task_ids())
        url = self.url + '/evidence/' + record['id']
        self.assertEqual(self.client.patch(url, json={'status': 'verified'}).json()['status'], 'verified')
        self.assertNotIn('evidence', self.task_ids())
        self.assertEqual(self.client.patch(url, json={'claim': 'Changed conclusion'}).json()['status'], 'pending')
        for fields in ({'page': 2}, {'page': 0}, {'document_id': '../outside'}, {'figure_id': 'f999'}, {'claim': ' '}, {'status': 'automatic'}):
            result = self.client.post(self.url + '/evidence', json={'claim': 'claim', 'page': 1, **fields})
            self.assertIn(result.status_code, (404, 422), result.text)
        self.assertEqual(len(self.client.get(self.url + '/evidence').json()), 1)
        self.assertEqual(self.client.delete(url).status_code, 200)
        self.assertEqual(self.client.get(self.url).json()['evidence_count'], 0)

    def test_removed_supplement_retains_evidence_but_cannot_be_verified(self):
        sup = self.supplement()
        main_fig = next(f for f in self.client.get(self.url + '/figures').json() if f['document_id'] == 'main')
        bad = self.client.post(self.url + '/evidence', json={'claim': 'bad', 'page': 1, 'document_id': sup['id'], 'figure_id': main_fig['id']})
        self.assertEqual(bad.status_code, 422)
        record = self.evidence(document_id=sup['id'], status='verified')
        self.client.delete(self.url + '/supplements/' + sup['id'])
        kept = self.client.get(self.url + '/evidence').json()[0]
        self.assertFalse(kept['source_available'])
        self.assertEqual(kept['status'], 'pending')
        url = self.url + '/evidence/' + record['id']
        self.assertEqual(self.client.patch(url, json={'status': 'verified'}).status_code, 404)
        self.assertEqual(self.client.patch(url, json={'status': 'rejected'}).status_code, 200)
        self.assertNotIn('evidence', self.task_ids())

    def test_replaced_main_requires_reverification_but_identical_pdf_does_not(self):
        record = self.evidence(status='verified')
        self.client.post(self.url + '/file', files={'file': ('same.pdf', fixture_pdf())})
        same = self.client.get(self.url + '/evidence').json()[0]
        self.assertFalse(same['source_changed'])
        self.assertEqual(same['status'], 'verified')
        self.client.post(self.url + '/file', files={'file': ('new.pdf', fixture_pdf(marker='REPLACED'))})
        changed = self.client.get(self.url + '/evidence').json()[0]
        self.assertTrue(changed['source_changed'])
        self.assertTrue(changed['source_available'])
        self.assertEqual(changed['status'], 'pending')
        unchanged = self.client.patch(self.url + '/evidence/' + record['id'], json={}).json()
        self.assertTrue(unchanged['source_changed'])
        self.assertEqual(unchanged['status'], 'pending')
        checked = self.client.patch(self.url + '/evidence/' + record['id'], json={'status': 'verified'}).json()
        self.assertFalse(checked['source_changed'])
        self.assertEqual(checked['status'], 'verified')


if __name__ == '__main__':
    unittest.main()
