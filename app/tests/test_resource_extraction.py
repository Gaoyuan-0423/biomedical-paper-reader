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
from paper_resources import resource_accessions


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
            self.assertEqual(item['notes'], '来自精读笔记自动整理。')
        self.assertEqual(body['paper']['resources_pending'], 13)
        self.assertNotIn('resources', {task['id'] for task in body['paper']['tasks']})
        self.assertNotIn('核查', body['message'])

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

    def test_notes_never_mutated_and_exports_keep_source_without_resource_status(self):
        notes = '## 数据与代码\n[本研究生成 GSE12345](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE12345)\n'
        self.save_notes(notes)
        before = (self.directory / 'meta.json').read_bytes()
        response = self.extract()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((self.directory / 'meta.json').read_bytes(), before)
        exported = self.client.get(self.url + '/export.md')
        self.assertEqual(exported.status_code, 200, exported.text)
        self.assertNotIn('核查状态', exported.text)
        self.assertIn('未关联原文', exported.text)
        self.assertIn('整理来源：精读笔记自动整理', exported.text)
        self.assertNotIn('尚未核对原文', exported.text)
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

    def test_reproduction_table_preserves_whole_rows_missing_ids_and_samples(self):
        self.save_notes('''## 研究材料
| 队列 | 编号 |
| --- | --- |
| 同一个 GEO 数据 | GSE11111、GSE22222、GSE99999 |
## 数据与复现入口
| 资源 | 用途 | 编号/链接 | 影响使用的条件 | 样品/材料 |
| --- | --- | --- | --- | --- |
| TCGA-STAD / TCGA GDC | 胃癌发现队列 | TCGA-STAD；TCGA GDC | 未报告具体下载批次 | 胃癌组织，纳入数量未报告 |
| GEO 验证队列 | 外部验证 | GSE11111、GSE22222 | 缺少筛选条件；不能等同仓库全部样本 | 论文使用样本数未报告 |
| 蛋白组数据 | 蛋白表达分析 | OMIX001073 | 需要申请数据 | 手术组织 |
| IMvigor210 | 免疫治疗验证 | IMvigor210 | 缺少本研究纳入病例说明 | 尿路上皮癌队列 |
| HGC-27、MKN-45 细胞系 | 体外验证 | | 未报告细胞来源编号 | HGC-27 / MKN-45 |
''')
        body = self.extract().json()
        self.assertEqual(body['added_count'], 5, body)
        resources = body['resources']
        geo = next(record for record in resources if record['title'] == 'GEO 验证队列')
        self.assertEqual(geo['identifiers'], 'GSE11111、GSE22222')
        self.assertEqual(geo['accession'], '')
        self.assertEqual(resource_accessions(geo), ['GSE11111', 'GSE22222'])
        self.assertNotIn('GSE99999', json.dumps(resources))
        self.assertEqual(geo['conditions'], '缺少筛选条件；不能等同仓库全部样本')
        self.assertEqual(geo['sample_details'], '论文使用样本数未报告')
        self.assertEqual(next(record for record in resources if record['title'] == '蛋白组数据')['accession'], 'OMIX001073')
        cell = next(record for record in resources if '细胞系' in record['title'])
        self.assertEqual((cell['kind'], cell['identifiers'], cell['url']), ('other', '', ''))
        for record in resources:
            self.assertEqual(record['auto_extraction']['source'], 'resource_table')
            self.assertNotIn('笔记片段', record['notes'])
            self.assertNotIn('|', record['notes'])
            self.assertEqual(record['status'], 'pending')

    def test_english_table_aliases_escaped_pipes_and_explicit_links(self):
        records = extract_resources('''## Data and reproducibility
| Resource (资源) | Purpose | Accession / link | Conditions affecting use | Samples |
| :--- | --- | ---: | --- | --- |
| **Analysis code** | source code | [repository](https://github.com/lab/code?ref=stable&x=1) | access\\|controlled<br>Version not reported | not applicable |
''')
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record['title'], 'Analysis code')
        self.assertEqual(record['conditions'], 'access|controlled\nVersion not reported')
        self.assertEqual(record['url'], 'https://github.com/lab/code?ref=stable&x=1')
        self.assertEqual(record['identifiers'], '[repository](https://github.com/lab/code?ref=stable&x=1)')
        self.assertEqual(record['kind'], 'code')

    def test_migration_backs_up_only_untouched_legacy_auto_and_is_idempotent(self):
        from resource_extraction import NOTICE
        def old_record(index, accession, **changes):
            raw = f'| GEO 数据 | {accession} |'
            record = main.ResourceIn(title='GEO · ' + accession, repository='GEO', accession=accession,
                                     notes=NOTICE + raw).model_dump()
            for field in ('purpose', 'identifiers', 'conditions', 'sample_details'):
                record.pop(field)
            record.update(id=str(index) * 32, created_at='2026-01-01', updated_at='2026-01-01', source_revision='')
            record.update(changes)
            return record
        original = [old_record(1, 'GSE11111'), old_record(2, 'GSE22222'),
                    old_record(3, 'GSE33333', status='verified'),
                    old_record(4, 'GSE44444', title='Human changed title'),
                    old_record(5, 'GSE55555', notes=NOTICE + '| GEO 数据 | GSE55555 |\n我的补充说明'),
                    old_record(6, 'GSE66666', custom_legacy_field='keep')]
        path = self.directory / 'resources.json'
        path.write_text(json.dumps(original, ensure_ascii=False))
        self.save_notes('''## 数据与复现入口
| 资源 | 用途 | 编号/链接 | 影响使用的条件 |
| --- | --- | --- | --- |
| GEO 验证队列 | 外部验证 | GSE11111、GSE22222 | 样本纳入条件未报告 |
''')
        body = self.extract().json()
        self.assertEqual((body['added_count'], body['updated_count'], body['backup_created']), (1, 2, True), body)
        backups = list(self.directory.glob('resources.backup-*.json'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text()), original)
        raw = json.loads(path.read_text())
        for preserved in original[2:]:
            self.assertEqual(next(record for record in raw if record['id'] == preserved['id']), preserved)
        self.assertEqual(len(raw), 5)
        before = path.read_bytes()
        repeated = self.extract().json()
        self.assertEqual((repeated['added_count'], repeated['updated_count']), (0, 0))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(list(self.directory.glob('resources.backup-*.json'))), 1)
        # v2 records used the old short notice in their fingerprints. A notice
        # wording update remains recognizable as automatic, with a backup, and
        # cannot make edited or previously verified rows replaceable.
        from resource_extraction import resource_fingerprint
        previous_version = json.loads(path.read_text())
        automatic = next(record for record in previous_version if record.get('auto_extraction'))
        automatic['notes'] = '来自精读笔记自动整理，尚未核对原文。'
        automatic['auto_extraction']['fingerprint'] = resource_fingerprint(automatic)
        path.write_text(json.dumps(previous_version, ensure_ascii=False))
        migrated = self.extract().json()
        self.assertEqual((migrated['added_count'], migrated['updated_count']), (1, 1))
        self.assertEqual(len(migrated['resources']), 5)
        migrated_raw = json.loads(path.read_text())
        for preserved in original[2:]:
            self.assertEqual(next(record for record in migrated_raw if record['id'] == preserved['id']), preserved)
        self.assertEqual(next(record for record in migrated_raw if record.get('auto_extraction'))['notes'], '来自精读笔记自动整理。')
        self.assertEqual(self.extract().json()['updated_count'], 0)

    def test_new_auto_updates_preserve_manual_edits_and_status(self):
        template = '''| 资源 | 用途 | 编号/链接 | 影响使用的条件 |
| --- | --- | --- | --- |
| GEO 队列 | 验证 | GSE11111 | %s |
| 代码 | 分析 | https://github.com/lab/code | 未报告版本 |
'''
        self.save_notes(template % '未报告样本量')
        first = self.extract().json()['resources']
        geo, code = first
        edited = self.client.patch(self.url + '/resources/' + code['id'], json={'purpose': '我的人工用途', 'status': 'verified'})
        self.assertEqual(edited.status_code, 200)
        self.assertNotIn('auto_extraction', edited.json())
        self.save_notes(template % '需筛选病理类型')
        response = self.extract().json()
        self.assertEqual((response['added_count'], response['updated_count']), (1, 1), response)
        self.assertEqual(len(response['resources']), 2)
        self.assertEqual(next(record for record in response['resources'] if record['id'] == code['id'])['purpose'], '我的人工用途')
        updated = next(record for record in response['resources'] if record['title'] == 'GEO 队列')
        self.assertNotEqual(updated['id'], geo['id'])
        self.assertEqual(updated['conditions'], '需筛选病理类型')

    def test_grouped_identifiers_link_each_paper_without_matching_generic_missing_text(self):
        self.save_notes('''| 资源 | 用途 | 编号/链接 | 影响使用的条件 |
| --- | --- | --- | --- |
| 联合队列 | 分析 | GSE11111、GSE22222、OMIX001073 | 需核对 |
| IMvigor210 | 验证 | 未报告 | 样本量未报告 |
''')
        self.extract()
        other_id, other = main.new_paper_dir()
        main.write_meta(other, {**main.blank_meta(other_id), 'title': 'Independent synthetic paper'})
        for fields in ({'title': 'Second accession', 'accession': 'GSE22222'},
                       {'title': 'OMIX', 'accession': 'OMIX001073'},
                       {'title': 'Unrelated cell line', 'identifiers': '未报告'}):
            response = self.client.post('/api/papers/' + other_id + '/resources', json=fields)
            self.assertEqual(response.status_code, 200, response.text)
        records = self.client.get('/api/resources').json()
        grouped = next(record for record in records if record['title'] == '联合队列')
        self.assertEqual([item['id'] for item in grouped['related_papers']], [other_id])
        for record in records:
            if record['title'] in ('Second accession', 'OMIX'):
                self.assertEqual([item['id'] for item in record['related_papers']], [self.pid])
            if record['identifiers'] == '未报告':
                self.assertEqual(record['related_papers'], [])

    def test_failed_backup_never_replaces_existing_records(self):
        self.save_notes('''| 资源 | 用途 | 编号/链接 | 影响使用的条件 |
| --- | --- | --- | --- |
| GEO 队列 | 分析 | GSE11111 | 旧条件 |
''')
        self.extract()
        path = self.directory / 'resources.json'
        before = path.read_bytes()
        self.save_notes(self.client.get(self.url).json()['notes'].replace('旧条件', '新条件'))
        atomic = main.atomic_json
        def fail_backup(path, value):
            if 'backup-' in path.name:
                raise OSError('synthetic disk error')
            return atomic(path, value)
        with patch.object(main, 'atomic_json', side_effect=fail_backup):
            with self.assertRaises(OSError):
                self.extract()
        self.assertEqual(path.read_bytes(), before)

    def test_accession_membership_ignores_unrelated_repository_search_routes(self):
        self.assertEqual(resource_accessions({'url': 'https://www.ncbi.nlm.nih.gov/pubmed/?term=GSE12345'}), [])
        self.assertEqual(resource_accessions({'url': 'https://www.ebi.ac.uk/search?acc=GSE12345'}), [])
        self.assertEqual(resource_accessions({'url': 'https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE12345'}), ['GSE12345'])
        self.assertEqual(resource_accessions({'identifiers': 'GSM1234、GSE12345；OMIX001073',
                                             'purpose': 'GSE99999'}), ['GSE12345', 'GSM1234', 'OMIX001073'])


if __name__ == '__main__':
    unittest.main()
