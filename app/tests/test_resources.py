"""Manual data/code records, shared identifiers and portable research exports."""
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from test_documents import fixture_pdf, main, TestClient


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bioreader-resources-')
        directory = Path(self.temp.name)
        self.patchers = [patch.object(main, 'DATA_DIR', directory),
                         patch.object(main, 'PAPERS_DIR', directory / 'papers'),
                         patch.object(main, 'SETTINGS_FILE', directory / 'settings.json')]
        for p in self.patchers:
            p.start()
        self.client = TestClient(main.app)
        response = self.client.post('/api/papers', files={'file': ('main.pdf', fixture_pdf())})
        self.assertEqual(response.status_code, 200, response.text)
        self.paper = response.json()
        self.url = '/api/papers/' + self.paper['id']
        self.directory = main.paper_dir(self.paper['id'])

    def tearDown(self):
        self.client.close()
        for p in self.patchers:
            p.stop()
        self.temp.cleanup()

    def add(self, **fields):
        response = self.client.post(self.url + '/resources', json={
            'title': 'Synthetic dataset', 'repository': 'GEO', 'accession': 'GSE123', **fields})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def supplement(self):
        response = self.client.post(self.url + '/supplements', files={
            'file': ('supp.pdf', fixture_pdf('Figure S1', 'SUPPLEMENT'))})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['supplements'][0]

    def test_manual_resource_roundtrip_has_no_verification_task_and_preserves_legacy_status(self):
        self.assertEqual(self.client.get(self.url + '/resources').json(), [])
        self.assertEqual(self.client.get('/api/resources').json(), [])
        original_notes = self.client.get(self.url).json()['notes']
        resource = self.add(role='generated', organism='human', tissue='liver', modality='scRNA-seq')
        self.assertEqual(resource['status'], 'pending')
        self.assertTrue(resource['source_available'])
        meta = self.client.get(self.url).json()
        self.assertEqual(meta['resources_count'], 1)
        self.assertEqual(meta['resources_pending'], 1)
        self.assertNotIn('resources', {t['id'] for t in meta['tasks']})
        url = self.url + '/resources/' + resource['id']
        checked = self.client.patch(url, json={'status': 'verified'}).json()
        self.assertEqual(checked['status'], 'verified')
        self.assertNotIn('resources', {t['id'] for t in self.client.get(self.url).json()['tasks']})
        changed = self.client.patch(url, json={'organism': 'mouse'}).json()
        self.assertEqual(changed['status'], 'verified')
        self.assertEqual(self.client.get(self.url).json()['notes'], original_notes)
        self.assertEqual(self.client.delete(url).status_code, 200)
        self.assertEqual(self.client.get(self.url).json()['resources_count'], 0)
        self.assertEqual(self.client.delete(url).status_code, 404)

    def test_structured_sample_roundtrip_validation_and_legacy_defaults(self):
        resource = self.add(organism='人', tissue='胃组织', disease=' 胃癌 ', modality='scRNA-seq',
                            platform='10x', sample_groups='肿瘤、正常', sample_count='肿瘤 8 例；正常 5 例')
        self.assertEqual(resource['disease'], '胃癌')
        self.assertEqual(resource['sample_count'], '肿瘤 8 例；正常 5 例')
        url = self.url + '/resources/' + resource['id']
        for body in ({'sample_count': None}, {'platform': []}, {'disease': 'x' * 1001}, {'sample_groups': 10}):
            result = self.client.patch(url, json=body)
            self.assertEqual(result.status_code, 422, result.text)
        saved = self.client.get(self.url + '/resources').json()[0]
        self.assertEqual(saved['sample_count'], resource['sample_count'])
        self.assertEqual(self.client.patch(url, json={'sample_groups': '', 'platform': ''}).status_code, 200)
        saved = self.client.get(self.url + '/resources').json()[0]
        self.assertEqual((saved['sample_groups'], saved['platform']), ('', ''))
        # Old sidecars gain defaults when read, without rewriting the file.
        path = self.directory / 'resources.json'
        original = json.loads(path.read_text())
        for field in ('disease', 'platform', 'sample_groups', 'sample_count'):
            original[0].pop(field, None)
        path.write_text(json.dumps(original))
        before = path.read_bytes()
        legacy = self.client.get(self.url + '/resources').json()[0]
        self.assertEqual([legacy[field] for field in ('disease', 'platform', 'sample_groups', 'sample_count')], [''] * 4)
        self.assertEqual(path.read_bytes(), before)

    def test_identifier_normalization_duplicates_and_edit_conflicts(self):
        first = self.add(repository='', accession=' gse123 ')
        self.assertEqual(first['repository'], 'GEO')
        self.assertEqual(first['accession'], 'GSE123')
        duplicate = self.client.post(self.url + '/resources', json={
            'title': 'same accession', 'repository': 'Gene Expression Omnibus', 'accession': 'GSE123'})
        self.assertEqual(duplicate.status_code, 409)
        second = self.add(accession='GSE456')
        conflict = self.client.patch(self.url + '/resources/' + second['id'], json={'accession': 'gse123'})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(len(self.client.get(self.url + '/resources').json()), 2)
        self.assertEqual(self.client.patch(self.url + '/resources/' + first['id'], json={}).status_code, 200)
        sra = self.add(repository='', accession=' srr123 ')
        self.assertEqual(sra['repository'], 'SRA')
        self.assertEqual(sra['accession'], 'SRR123')
        duplicate_sra = self.client.post(self.url + '/resources', json={
            'title': 'Duplicate SRA', 'repository': 'NCBI SRA', 'accession': 'SRR123'})
        self.assertEqual(duplicate_sra.status_code, 409)

    def test_url_only_code_and_normalized_url_duplicates(self):
        record = self.add(kind='code', accession='', repository='GitHub', url='HTTPS://EXAMPLE.ORG:443/code#readme', role='implementation')
        self.assertEqual(record['url'], 'https://example.org/code#readme')
        response = self.client.post(self.url + '/resources', json={'title': 'Duplicate code', 'url': 'https://example.org/code#install'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(record['role'], 'implementation')

    def test_structured_rows_keep_legacy_accession_and_url_in_duplicate_identity(self):
        first = self.add(title='Validation cohorts', purpose='External validation', accession='GSE123')
        second = self.add(title='Validation cohorts', purpose='External validation', accession='GSE456')
        self.assertNotEqual(first['id'], second['id'])
        duplicate = self.client.post(self.url + '/resources', json={
            'title': 'Validation cohorts', 'purpose': 'Updated use', 'accession': 'gse123'})
        self.assertEqual(duplicate.status_code, 409)
        one = self.add(title='Analysis code', kind='code', repository='GitHub', accession='',
                       purpose='Reproduce analysis', url='https://example.org/code-a#readme')
        two = self.add(title='Analysis code', kind='code', repository='GitHub', accession='',
                       purpose='Reproduce analysis', url='https://example.org/code-b')
        self.assertNotEqual(one['id'], two['id'])
        duplicate_url = self.client.post(self.url + '/resources', json={
            'title': 'Analysis code', 'purpose': 'Reproduce analysis',
            'url': 'HTTPS://EXAMPLE.ORG:443/code-a#install'})
        self.assertEqual(duplicate_url.status_code, 409)
        different_path = self.add(title='Analysis code', kind='code', repository='GitHub', accession='',
                                  purpose='Reproduce analysis', url='https://example.org/Code-a')
        self.assertNotEqual(one['id'], different_path['id'])
        self.assertEqual(len(self.client.get(self.url + '/resources').json()), 5)

    def test_invalid_fields_links_and_sources_never_write(self):
        bad_fields = [
            {'title': ' '}, {'accession': '', 'url': ''}, {'kind': 'auto'}, {'role': 'invented'},
            {'url': 'javascript:alert(1)'}, {'url': 'file:///tmp/a'}, {'url': '//example.org/a'},
            {'url': 'https://user:pass@example.org/a'}, {'url': 'https://example.org/\nattack'},
            {'url': 'https://example.org\\@evil.test'}, {'url': 'https://example.org:bad'},
            {'document_id': '../other', 'page': 1}, {'document_id': 'main'}, {'page': 1},
            {'document_id': 'main', 'page': 2}, {'document_id': 'main', 'page': True},
            {'document_id': 'f' * 32, 'page': 1}, {'accession': 'GSE123 GSE456'},
        ]
        for fields in bad_fields:
            with self.subTest(fields=fields):
                result = self.client.post(self.url + '/resources', json={'title': 'Bad', 'accession': 'GSE123', **fields})
                self.assertIn(result.status_code, (404, 422), result.text)
        self.assertEqual(self.client.get(self.url + '/resources').json(), [])
        record = self.add()
        for fields in ({'title': None}, {'status': None}, {'url': None}):
            self.assertEqual(self.client.patch(self.url + '/resources/' + record['id'], json=fields).status_code, 422)

    def test_shared_accession_keeps_per_paper_role_and_queries_related_papers(self):
        first = self.add(role='generated', tissue='liver', repository='')
        pid, directory = main.new_paper_dir()
        meta = main.blank_meta(pid)
        meta['title'] = 'Independent reuse study'
        main.write_meta(directory, meta)
        second = self.client.post('/api/papers/' + pid + '/resources', json={
            'title': 'Reuse resource', 'repository': 'geo', 'accession': 'gse123', 'role': 'reused', 'tissue': 'blood'})
        self.assertEqual(second.status_code, 200)
        records = self.client.get('/api/resources', params={'q': 'gse123'}).json()
        self.assertEqual(len(records), 2)
        own = next(r for r in records if r['id'] == first['id'])
        self.assertEqual(own['role'], 'generated')
        self.assertEqual(own['related_papers'], [{'id': pid, 'title': 'Independent reuse study'}])
        filtered = self.client.get('/api/resources', params={'q': 'liver'}).json()
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]['related_papers'], own['related_papers'])
        self.assertEqual(next(r for r in records if r['paper_id'] == pid)['role'], 'reused')
        self.assertEqual(self.client.get('/api/resources', params={'q': 'no-match'}).json(), [])

    def test_missing_source_preserved_and_can_be_detached(self):
        supplement = self.supplement()
        record = self.add(document_id=supplement['id'], page=1, quote='Original supplemental statement', status='verified')
        self.client.delete(self.url + '/supplements/' + supplement['id'])
        retained = self.client.get(self.url + '/resources').json()[0]
        self.assertFalse(retained['source_available'])
        self.assertEqual(retained['status'], 'verified')  # Legacy state is not a workflow.
        url = self.url + '/resources/' + record['id']
        self.assertEqual(self.client.patch(url, json={'status': 'verified'}).status_code, 404)
        self.assertEqual(self.client.patch(url, json={'notes': 'Need to recover source'}).status_code, 200)
        detached = self.client.patch(url, json={'document_id': '', 'page': None, 'status': 'verified'}).json()
        self.assertEqual(detached['status'], 'verified')
        self.assertTrue(detached['source_available'])
        self.assertEqual(detached['document_id'], '')

    def test_replaced_source_warns_without_resetting_legacy_status(self):
        record = self.add(document_id='main', page=1, status='verified')
        self.client.post(self.url + '/file', files={'file': ('replacement.pdf', fixture_pdf(marker='REPLACED'))})
        url = self.url + '/resources/' + record['id']
        changed = self.client.patch(url, json={}).json()
        self.assertTrue(changed['source_changed'])
        self.assertEqual(changed['status'], 'verified')
        self.assertNotIn('resources', {t['id'] for t in self.client.get(self.url).json()['tasks']})
        checked = self.client.patch(url, json={'status': 'verified'}).json()
        self.assertEqual(checked['status'], 'verified')
        self.assertFalse(checked['source_changed'])

    def test_resource_summary_is_derived_and_not_saved_into_paper_metadata(self):
        self.add()
        self.client.patch(self.url, json={'reading_status': 'reading'})
        stored = json.loads((self.directory / 'meta.json').read_text())
        self.assertNotIn('resources_count', stored)
        self.assertNotIn('resources_pending', stored)
        self.assertNotIn('tasks', stored)
        listed = self.client.get('/api/papers').json()[0]
        self.assertEqual(listed['resources_pending'], 1)

    def test_export_preview_zip_and_markdown_include_records_without_mutation(self):
        self.client.patch(self.url, json={'title': '数据与证据', 'notes': '# My report\n\nFigure 1 describes the result.'})
        self.add(url='https://example.org/data', document_id='main', page=1, role='generated')
        self.client.post(self.url + '/evidence', json={'claim': 'Synthetic claim', 'page': 1, 'status': 'verified'})
        before = (self.directory / 'meta.json').read_bytes()
        draft = '# Unsaved draft\n\nFigure 1 is relevant.'
        prepared = self.client.post(self.url + '/notes/export-preview', json={'notes': draft}).json()['notes']
        self.assertTrue(prepared.startswith(draft))
        self.assertIn('Synthetic dataset', prepared)
        self.assertIn('Synthetic claim', prepared)
        self.assertIn('GSE123', prepared)
        again = self.client.post(self.url + '/notes/export-preview', json={'notes': prepared}).json()['notes']
        self.assertEqual(again, prepared)
        response = self.client.post(self.url + '/export.zip', json={'notes': draft})
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else '')
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            text = archive.read('report.md').decode('utf-8')
            self.assertIn('GSE123', text)
            self.assertIn('Synthetic claim', text)
            self.assertTrue(any(name.startswith('assets/') for name in archive.namelist()))
        markdown = self.client.get(self.url + '/export.md')
        self.assertEqual(markdown.status_code, 200)
        self.assertIn('GSE123', markdown.text)
        self.assertIn('# My report', markdown.text)
        self.assertEqual((self.directory / 'meta.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
