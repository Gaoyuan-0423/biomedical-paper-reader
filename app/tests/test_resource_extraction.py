"""Offline saved-note extraction: conservative identifiers and no data loss."""
import concurrent.futures
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from test_documents import main, TestClient
from resource_extraction import extract_resources


class ResourceExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bioreader-extraction-')
        directory = Path(self.temp.name)
        self.patchers = [patch.object(main, 'DATA_DIR', directory),
                         patch.object(main, 'PAPERS_DIR', directory / 'papers'),
                         patch.object(main, 'SETTINGS_FILE', directory / 'settings.json')]
        for patcher in self.patchers:
            patcher.start()
        self.client = TestClient(main.app)
        self.pid, self.directory = main.new_paper_dir()
        self.meta = main.blank_meta(self.pid)
        self.meta.update(title='Synthetic offline paper', notes_mode='deep',
                         notes='## 数据与代码\n本研究生成 GSE12345。')
        main.write_meta(self.directory, self.meta)
        self.url = '/api/papers/' + self.pid

    def tearDown(self):
        self.client.close()
        for patcher in self.patchers:
            patcher.stop()
        self.temp.cleanup()

    def save_notes(self, notes, **fields):
        meta = main.read_meta(self.directory)
        meta.update(notes=notes, **fields)
        main.write_meta(self.directory, meta)

    def extract(self):
        return self.client.post(self.url + '/resources/extract')

    def test_unicode_markdown_tables_known_identifiers_and_explicit_roles(self):
        self.save_notes('''## 方法与数据资源
| 关系 | 仓库编号或代码 |
| --- | --- |
| 本研究生成 | ＧＳＥ１２３４５；GSM5678 |
| 复用已有数据 | SRP100、SRR200、ERP300、ERR400、PRJNA500、PRJEB600 |
| 关系未明 | CRA700、HRA800、phs000009.v2.p1、E-MTAB-1000 |
| 分析代码 | [代码](https://github.com/lab/project?download=1&format=tsv#readme) |
''')
        response = self.extract()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['added_count'], 13)
        by_id = {item['accession']: item for item in body['resources'] if item['accession']}
        self.assertEqual(by_id['GSE12345']['role'], 'generated')
        self.assertEqual(by_id['ERP300']['repository'], 'ENA')
        self.assertEqual(by_id['PRJNA500']['repository'], 'BioProject')
        self.assertEqual(by_id['HRA800']['repository'], 'GSA')
        self.assertEqual(by_id['PHS000009.V2.P1']['repository'], 'dbGaP')
        self.assertEqual(by_id['E-MTAB-1000']['repository'], 'ArrayExpress')
        self.assertEqual(by_id['SRP100']['role'], 'reused')
        code = next(item for item in body['resources'] if item['kind'] == 'code')
        self.assertEqual(code['role'], 'implementation')
        self.assertEqual(code['url'], 'https://github.com/lab/project?download=1&format=tsv#readme')
        for item in body['resources']:
            self.assertEqual(item['status'], 'pending')
            self.assertEqual((item['document_id'], item['page'], item['quote']), ('', None, ''))
            self.assertIn('尚未核对原文', item['notes'])
        self.assertEqual(body['paper']['resources_pending'], 13)
        self.assertIn('resources', {task['id'] for task in body['paper']['tasks']})

    def test_official_accession_url_merges_without_binding_unrelated_links(self):
        self.save_notes('''## 结果
GSE12345。
## 数据与代码
本研究生成 GSE12345，[仓库](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE12345&view=full)。
代码实现 https://github.com/lab/other，关联关系未知。
复用 GSE99999 以及本研究生成 GSE88888。
''')
        body = self.extract().json()
        self.assertEqual(body['added_count'], 4)
        record = next(item for item in body['resources'] if item['accession'] == 'GSE12345')
        self.assertEqual(record['url'], 'https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE12345&view=full')
        self.assertEqual(record['role'], 'generated')  # Preferred data section wins.
        self.assertTrue(all(item['role'] == 'unknown' for item in body['resources'] if item['accession'] in ('GSE99999', 'GSE88888')))
        self.assertEqual(next(item for item in body['resources'] if item['kind'] == 'code')['accession'], '')
        repeat = self.extract().json()
        self.assertEqual((repeat['added_count'], repeat['skipped_count']), (0, 4))

    def test_reject_placeholders_papers_untrusted_hosts_and_proposals(self):
        notes = '''## 数据
GSEXXXX SRPxxx GSE123ABC prefixGSE123 GSE123_suffix E-FAKE-123.
https://doi.org/10.1000/paper https://example.org/data/GSE12345
https://github.com.evil.test/lab/repo https://user:secret@github.com/lab/repo
https://github.com/topics/single-cell https://zenodo.org/search?q=GSE54321
例如 GSE99999，可能是一个示例。
```python
GSE77777 https://github.com/lab/placeholder
```
## 未来实验建议
下载 GSE88888 和 https://github.com/lab/future
### 数据与代码
GSE66666
## 参考文献
GSE55555
'''
        self.save_notes(notes)
        before = (self.directory / 'meta.json').read_bytes()
        body = self.extract().json()
        self.assertEqual(body['added_count'], 0)
        self.assertIn('未找到明确', body['message'])
        self.assertFalse((self.directory / 'resources.json').exists())
        self.assertEqual((self.directory / 'meta.json').read_bytes(), before)

    def test_resource_specific_links_preserve_query_and_do_not_invent_accessions(self):
        notes = '''## 数据与代码
https://zenodo.org/records/123?download=1&version=2
https://figshare.com/articles/dataset/synthetic/456?x=%26&y=two
https://datadryad.org/stash/dataset/doi:10.5061/dryad.abc123
https://doi.org/10.5281/zenodo.789
https://gitlab.com/group/project/-/tree/main?ref=v1&label=中文
'''
        records = extract_resources(notes)
        self.assertEqual(len(records), 5)
        self.assertTrue(all(item['accession'] == '' for item in records))
        self.assertTrue(all(item['role'] == 'unknown' for item in records))
        self.assertIn('https://figshare.com/articles/dataset/synthetic/456?x=%26&y=two', {item['url'] for item in records})
        self.assertIn('https://gitlab.com/group/project/-/tree/main?ref=v1&label=中文', {item['url'] for item in records})
        self.assertTrue(all(item['kind'] == 'other' for item in records if item['repository'] == 'Zenodo'))
        self.assertTrue(all(item['kind'] == 'dataset' for item in records if item['repository'] in ('Figshare', 'Dryad')))

    def test_general_repository_pages_do_not_imply_data_type(self):
        records = extract_resources('''## 数据与代码
https://figshare.com/articles/software/synthetic/123
https://figshare.com/collections/synthetic/456
https://figshare.com/articles/journal_contribution/paper/111
https://doi.org/10.6084/m9.figshare.789
https://doi.org/10.5061/dryad.abc123
''')
        by_url = {item['url']: item['kind'] for item in records}
        self.assertEqual(by_url, {
            'https://figshare.com/articles/software/synthetic/123': 'code',
            'https://figshare.com/collections/synthetic/456': 'other',
            'https://doi.org/10.6084/m9.figshare.789': 'other',
            'https://doi.org/10.5061/dryad.abc123': 'dataset',
        })

    def test_manual_verified_records_unchanged_and_url_only_existing_deduplicates(self):
        records = []
        for fields in (
            {'title': 'Human title', 'repository': 'GEO', 'accession': 'GSE12345', 'role': 'reused', 'notes': 'Human observation'},
            {'title': 'Existing URL', 'url': 'https://www.ebi.ac.uk/ena/browser/view/ERP22222?download=1'},
        ):
            result = self.client.post(self.url + '/resources', json={**fields, 'status': 'verified'})
            self.assertEqual(result.status_code, 200, result.text)
            records.append(result.json())
        self.save_notes('## 数据\n本研究生成 GSE12345。\n复用 ERP22222。\nGSE33333。')
        body = self.extract().json()
        self.assertEqual((body['added_count'], body['skipped_count']), (1, 2))
        for original in records:
            self.assertEqual(next(item for item in body['resources'] if item['id'] == original['id']), original)
        self.assertEqual(body['paper']['resources_pending'], 1)

    def test_legacy_human_fields_preserved_exactly_when_appending(self):
        old = {'id': 'b' * 32, 'title': '  Human title  ', 'accession': 'gse77777',
               'status': 'verified', 'document_id': '', 'page': None,
               'notes': '  Human spacing  ', 'custom_legacy_field': {'keep': True}}
        path = self.directory / 'resources.json'
        path.write_text(json.dumps([old]))
        body = self.extract().json()
        self.assertEqual(body['added_count'], 1)
        self.assertEqual(json.loads(path.read_text())[0], old)

    def test_only_saved_complete_current_deep_notes_allowed(self):
        for fields, status, phrase in (
            ({'notes_mode': 'quick'}, 400, '精读'),
            ({'notes': ' '}, 400, '精读'),
            ({'notes_incomplete': True}, 409, '尚未完成'),
            ({'material_revision': 2, 'notes_material_revision': 1}, 409, '材料已变化'),
        ):
            with self.subTest(fields=fields):
                meta = dict(self.meta)
                meta.update(fields)
                main.write_meta(self.directory, meta)
                response = self.extract()
                self.assertEqual(response.status_code, status, response.text)
                self.assertIn(phrase, response.json()['detail'])
                self.assertFalse((self.directory / 'resources.json').exists())
        main.write_meta(self.directory, self.meta)
        # A metadata-only legacy record is sufficient; extraction does not need
        # a file, API key, other paper, or any remote service.
        self.assertFalse(self.meta['has_file'])
        self.assertEqual(self.extract().status_code, 200)

    def test_corrupt_sidecar_never_replaced_and_export_remains_blocked(self):
        path = self.directory / 'resources.json'
        for damaged in ('{bad json', '[{}]', '{}'):
            with self.subTest(damaged=damaged):
                path.write_text(damaged)
                response = self.extract()
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(path.read_text(), damaged)
                self.assertEqual(self.client.get(self.url + '/export.md').status_code, 409)
                self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_notes_never_mutated_and_exports_label_pending_without_pdf_provenance(self):
        notes = '## 数据与代码\n[本研究生成 GSE12345](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE12345)\n'
        self.save_notes(notes)
        before = (self.directory / 'meta.json').read_bytes()
        response = self.extract()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((self.directory / 'meta.json').read_bytes(), before)
        exported = self.client.get(self.url + '/export.md')
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertIn('待核查', exported.text)
        self.assertIn('未关联原文', exported.text)
        self.assertIn('尚未核对原文', exported.text)
        self.assertNotIn('PDF 第', exported.text)
        # Exported appendices re-imported as notes must not recreate deleted items.
        self.assertEqual(extract_resources('## 精读\n没有资源。\n\n## 阅读核查与资源记录\n### 数据与代码\nGSE99999'), [])

    def test_concurrent_extracts_are_idempotent(self):
        barrier = threading.Barrier(2)

        def run():
            barrier.wait(timeout=5)
            return main.extract_paper_resources(self.pid)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run) for _ in range(2)]
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual(sorted(item['added_count'] for item in results), [0, 1])
        self.assertEqual(len(json.loads((self.directory / 'resources.json').read_text())), 1)


if __name__ == '__main__':
    unittest.main()
