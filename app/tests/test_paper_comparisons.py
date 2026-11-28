"""End-to-end project comparisons, paper-scoped source snapshots and revision conflicts."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_documents import fixture_pdf, main, TestClient


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='bioreader-comparison-')
        self.addCleanup(temp.cleanup)
        self.data = Path(temp.name)
        for key, value in [('DATA_DIR', self.data), ('PAPERS_DIR', self.data/'papers'), ('TRASH_DIR', self.data/'trash')]:
            patcher = patch.object(main, key, value); patcher.start(); self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.papers = []
        for i in (1, 2, 3):
            self.papers.append(self.client.post('/api/papers', files={'file': (f'paper{i}.pdf', fixture_pdf(f'Figure {i}', f'PAPER{i}'))}).json()['id'])
        self.a, self.b, self.out = self.papers
        self.library = self.client.post('/api/project-libraries', json={'name':'Cohorts', 'paper_ids':[self.a,self.b]}).json()['id']
        self.base = '/api/comparisons/' + self.library
        self.url = self.base + '/' + 'c'*32
        self.body = {'revision':0, 'question':'Which cohorts support the finding?', 'synthesis':'Reader synthesis',
                     'rows':[{'paper_id':self.a, 'relation':'qualifies', 'population':'Six adults', 'contrast':'Cases vs controls', 'note':'Small cohort'},
                             {'paper_id':self.b, 'relation':'unassessed'}]}

    def get_material(self, pid):
        result = self.client.get(self.base+'/material/'+pid)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def numeric_answer(self, paper_id, value, extra_rows=0):
        from test_supplement_tables import workbook
        url = '/api/papers/' + paper_id
        data = workbook({'Effects': [['Gene', 'log2FC'], *([[]] * extra_rows), ['FLC', value]]})
        upload = self.client.post(url + '/supplements', files={'file': ('Table S2.xlsx', data)})
        self.assertEqual(upload.status_code, 200, upload.text)
        answer = self.client.post(url + '/chat', json={'message': 'Table S2.xlsx 中 FLC 的 log2FC 是多少?'})
        self.assertEqual(answer.status_code, 200, answer.text)
        assistant = self.client.get(url + '/chat').json()['messages'][-1]
        self.assertEqual(assistant['cited_source_ids'], ['S1'])
        return {'paper_id': paper_id, 'message_id': assistant['id'], 'source_id': 'S1'}

    def test_table_measurements_persist_by_paper_and_reopen_without_chat(self):
        self.assertEqual(self.client.put(self.url, json=self.body).status_code, 200)
        a = self.numeric_answer(self.a, 1.5)
        b = self.numeric_answer(self.b, -3.25, extra_rows=2)
        route = self.url + '/table-measurements'
        self.assertEqual(self.client.post(route, json={**a, 'paper_id': self.b, 'revision': 1}).status_code, 404)
        self.assertEqual(self.client.post(route, json={**a, 'paper_id': self.out, 'revision': 1}).status_code, 404)
        params = {**a, 'revision': 1, 'field_label': '表达变化', 'direction': '病例 / 对照'}
        first = self.client.post(route, json=params)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(self.client.post(route, json=params).json(), first.json())
        self.assertEqual(self.client.post(route, json={**a, 'revision': 1}).json(), first.json())
        self.assertEqual(self.client.post(route, json={**b, 'revision': 1}).status_code, 409)
        saved = self.client.post(route, json={**b, 'revision': 2})
        self.assertEqual(saved.status_code, 200, saved.text)
        cells = [r['table_measurements'][0] for r in saved.json()['rows']]
        self.assertEqual([c['source']['value'] for c in cells], ['1.5', '-3.25'])
        self.assertEqual([c['source']['row'] for c in cells], [2, 4])
        self.assertEqual([c['field_label'] for c in cells], ['表达变化', 'log2FC'])
        self.assertEqual(cells[0]['direction'], '病例 / 对照')

        unrelated = self.client.put(self.url, json={**self.body, 'revision': 3, 'synthesis': 'Updated notes'})
        self.assertEqual(unrelated.status_code, 200, unrelated.text)
        self.assertEqual([r['table_measurements'][0] for r in unrelated.json()['rows']], cells)
        self.assertEqual(self.client.get(self.base, params={'paper_id': self.out}).status_code, 404)
        self.assertEqual(len(self.client.get(self.base, params={'paper_id': self.a}).json()), 1)
        for pid, cell in zip((self.a, self.b), cells):
            (main.paper_dir(pid) / 'chat.json').unlink()
            preview = self.client.get(f"{route}/{pid}/{cell['id']}/source")
            self.assertEqual(preview.status_code, 200, preview.text)
            src = preview.json()['source']
            self.assertEqual((src['paper_id'], src['sheet'], src['value']), (pid, 'Effects', cell['source']['value']))
            raw = next(r for r in src['rows'] if r['number'] == src['row'])
            self.assertEqual(raw['cells'][src['column'] - src['first_column']], src['value'])
        self.assertEqual(self.client.get(f"{route}/{self.a}/{cells[1]['id']}/source").status_code, 404)
        (main.paper_dir(self.a) / 'supplement-files' / cells[0]['source']['revision']).write_bytes(b'changed')
        self.assertEqual(self.client.get(f"{route}/{self.a}/{cells[0]['id']}/source").status_code, 409)
        self.assertEqual(self.client.get(self.url).json()['rows'][0]['table_measurements'][0], cells[0])

    def test_copy_or_remove_only_explicitly_selected_measurement_snapshots(self):
        self.client.put(self.url, json=self.body)
        a = self.numeric_answer(self.a, 0.2)
        route = self.url + '/table-measurements'
        cell = self.client.post(route, json={**a, 'revision': 1}).json()['rows'][0]['table_measurements'][0]
        ref = {k: cell[k] for k in ('id', 'snapshot')}
        destination = self.base + '/' + 'd' * 32
        origin = 'c' * 32
        copy = {**self.body, 'copy_measurements_from': origin,
                'rows': [{**self.body['rows'][0], 'table_measurement_refs': [ref]}, self.body['rows'][1]]}
        self.assertEqual(self.client.put(destination, json={**copy, 'rows': self.body['rows']}).status_code, 409)
        wrong_owner = [dict(copy['rows'][0], table_measurement_refs=[]),
                       dict(copy['rows'][1], table_measurement_refs=[ref])]
        self.assertEqual(self.client.put(destination, json=dict(copy, rows=wrong_owner)).status_code, 409)
        new_lib = self.client.post('/api/project-libraries', json={'name': 'Other', 'paper_ids': [self.a,self.b]}).json()['id']
        self.assertEqual(self.client.put(destination.replace(self.library, new_lib), json=copy).status_code, 404)
        relabel = self.client.post(route, json={**a, 'revision': 2, 'direction': 'treatment over control'})
        new_cell = relabel.json()['rows'][0]['table_measurements'][0]
        self.assertNotEqual(new_cell['snapshot'], ref['snapshot'])
        self.assertEqual(self.client.put(destination, json=copy).status_code, 409)
        self.assertEqual(self.client.get(destination).status_code, 404)
        ref['snapshot'] = new_cell['snapshot']
        duplicate = self.client.put(destination, json=copy)
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertEqual(duplicate.json()['rows'][0]['table_measurements'], [new_cell])
        self.assertEqual(self.client.put(destination, json=copy).json(), duplicate.json())
        remove = {**self.body, 'revision': 1, 'rows': [dict(copy['rows'][0], table_measurement_refs=[]), self.body['rows'][1]]}
        self.assertEqual(self.client.put(destination, json=remove).status_code, 200)
        self.assertFalse(self.client.get(destination).json()['rows'][0].get('table_measurements'))
        self.assertEqual(self.client.get(self.url).json()['rows'][0]['table_measurements'], [new_cell])

    def test_review_legacy_direction_with_stable_provenance_and_revision(self):
        self.client.put(self.url, json=self.body)
        address = self.numeric_answer(self.a, -0.73)
        route = self.url + '/table-measurements'
        old = self.client.post(route, json={**address, 'revision':1, 'direction':'control vs treated'}).json()
        original = old['rows'][0]['table_measurements'][0]
        self.assertNotIn('contrast_definition', original)
        ref = {k: original[k] for k in ('id','snapshot')}
        body = dict(self.body, revision=2, rows=[dict(self.body['rows'][0], table_measurement_refs=[ref]), self.body['rows'][1]])
        invalid = dict(ref, contrast_definition={'test_group':'treated', 'reference_group':' treated ', 'scale':'log2fc'})
        body['rows'][0]['table_measurement_refs'] = [invalid]
        self.assertEqual(self.client.put(self.url, json=body).status_code, 422)
        definition = {'test_group':'control', 'reference_group':'treated', 'scale':'log2fc'}
        body['rows'][0]['table_measurement_refs'] = [dict(ref, contrast_definition=definition)]
        saved = self.client.put(self.url, json=body)
        self.assertEqual(saved.status_code, 200, saved.text)
        reviewed = saved.json()['rows'][0]['table_measurements'][0]
        self.assertEqual(reviewed['contrast_definition'], definition)
        self.assertNotEqual(reviewed['snapshot'], original['snapshot'])
        self.assertEqual(reviewed['source'], original['source'])
        self.assertEqual(reviewed['direction'], original['direction'])
        self.assertEqual(self.client.put(self.url, json=body).json(), saved.json())
        self.assertEqual(self.client.put(self.url, json=dict(body,revision=3)).status_code, 409)
        self.assertEqual(self.client.get(self.url).json()['rows'][0]['table_measurements'][0], reviewed)
        second = self.client.post(route, json={**address,'revision':3})
        self.assertEqual(second.json(), saved.json())

    def test_add_new_paper_and_original_measurement_as_one_transaction(self):
        self.client.put(self.url, json=self.body)
        answer = self.numeric_answer(self.out, 3)
        route = self.url + '/table-measurements'
        params = {**answer, 'add_paper': True, 'revision': 1}
        self.assertEqual(self.client.post(route, json=params).status_code, 404)
        self.client.put('/api/project-libraries/' + self.library,
                        json={'revision': 1, 'name': 'Cohorts', 'add': [self.out]})
        self.assertEqual(self.client.get(self.base).json()[0]['paper_ids'], [self.a, self.b])
        self.assertEqual(self.client.get(self.base, params={'paper_id': self.out}).json(), [])
        self.assertEqual(self.client.post(route, json={**params, 'add_paper': False}).status_code, 404)
        self.assertEqual(self.client.post(route, json={**params, 'revision': 2}).status_code, 409)
        self.assertEqual(self.client.get(self.url).json()['revision'], 1)
        self.assertEqual(self.client.get(self.base).json()[0]['paper_ids'], [self.a, self.b])
        response = self.client.post(route, json=params)
        self.assertEqual(response.status_code, 200, response.text)
        new = response.json()
        self.assertEqual(new['revision'], 2)
        self.assertEqual([r['paper_id'] for r in new['rows']], [self.a, self.b, self.out])
        self.assertEqual(new['rows'][2]['table_measurements'][0]['source']['value'], '3')
        self.assertEqual(self.client.post(route, json=params).json(), new)
        self.assertEqual(self.client.get(self.base, params={'paper_id': self.out}).json()[0]['paper_ids'],
                         [self.a, self.b, self.out])

    def test_attachment_replaced_between_parse_and_commit_is_rejected(self):
        self.client.put(self.url, json=self.body)
        answer = self.numeric_answer(self.a, 2.1)
        verify = main.supplement_tables.verified_table_source
        def replace_after_read(directory, meta, source):
            preview = verify(directory, meta, source)
            (directory / 'supplement-files' / source['revision']).write_bytes(b'changed while reading')
            return preview
        with patch.object(main.supplement_tables, 'verified_table_source', side_effect=replace_after_read):
            result = self.client.post(self.url + '/table-measurements', json={**answer, 'revision': 1})
        self.assertEqual(result.status_code, 409)
        old = self.client.get(self.url).json()
        self.assertEqual(old['revision'], 1)
        self.assertNotIn('table_measurements', old['rows'][0])

    def test_simultaneous_adoptions_use_the_comparison_revision(self):
        self.client.put(self.url, json=self.body)
        a, b = self.numeric_answer(self.a, 2), self.numeric_answer(self.b, -1)
        barrier = Barrier(2)
        verify = main.supplement_tables.verified_table_source
        def verify_together(*args):
            source = verify(*args)
            barrier.wait(timeout=5)
            return source
        def adopt(body):
            return self.client.post(self.url + '/table-measurements', json={**body, 'revision': 1}).status_code
        with patch.object(main.supplement_tables, 'verified_table_source', side_effect=verify_together):
            with ThreadPoolExecutor(max_workers=2) as pool:
                codes = list(pool.map(adopt, (a, b)))
        self.assertEqual(sorted(codes), [200, 409])
        result = self.client.get(self.url).json()
        self.assertEqual(result['revision'], 2)
        self.assertEqual(sum(len(r.get('table_measurements', [])) for r in result['rows']), 1)

    def test_scope_matrix_and_idempotent_concurrent_edits(self):
        self.assertEqual(self.client.get(self.base+'/material/'+self.out).status_code, 404)
        outside = dict(self.body, rows=[self.body['rows'][0], {'paper_id':self.out}])
        self.assertEqual(self.client.put(self.url,json=outside).status_code, 409)
        self.assertEqual(self.client.put(self.url,json=dict(self.body, rows=self.body['rows'][:1])).status_code, 422)
        self.assertEqual(self.client.put(self.url,json=dict(self.body, rows=self.body['rows'][:1]*2)).status_code, 422)
        saved = self.client.put(self.url, json=self.body)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(self.client.put(self.url, json=self.body).json(), saved.json())
        self.assertEqual(self.client.get(self.url).json()['rows'][0]['contrast'], 'Cases vs controls')
        self.assertEqual(self.client.get(self.url).json()['rows'][0]['population'], 'Six adults')
        changed = self.client.put(self.url,json=dict(self.body, revision=1, synthesis='Follow-up'))
        self.assertEqual(changed.json()['revision'], 2)
        self.assertEqual(self.client.put(self.url,json=dict(self.body, revision=1, synthesis='From other window')).status_code, 409)
        self.assertEqual(self.client.get(self.url).json()['synthesis'], 'Follow-up')
        self.client.put(self.base+'/'+'d'*32,json=dict(self.body, question='Another question'))
        self.assertEqual({s['question'] for s in self.client.get(self.base).json()}, {'Another question',self.body['question']})
        self.assertEqual(self.client.get('/api/comparisons/all').json(), [])

    def test_verified_supplement_figures_cards_pages_and_replaced_bytes(self):
        paper = '/api/papers/'+self.a
        doc = self.client.post(paper+'/supplements',files={'file':('sup.pdf',fixture_pdf('Figure S1','supplement A'))}).json()['supplements'][0]['id']
        resource = self.client.post(paper+'/resources',json={'title':'study cohort', 'sample_groups':'cases vs controls',
                                                            'sample_count':'6 donors','organism':'human','accession':'GSE111'})
        self.assertEqual(resource.status_code, 200, resource.text)
        material = self.get_material(self.a)
        self.assertEqual(material['samples'][0]['sample_count'], '6 donors')
        self.assertEqual(material['accessions'], ['GSE111'])
        fig = next(f for f in material['sources'] if f['kind'] == 'figure' and f['document_id'] == doc)
        created = self.client.post(paper+'/evidence',json={'claim':'Reader judgment','quote':'Actual recorded quotation',
                                                         'document_id':doc,'page':1,'figure_id':fig['figure_id'],'status':'verified'})
        self.assertEqual(created.status_code, 200, created.text)
        material = self.get_material(self.a)
        card = next(f for f in material['sources'] if f['kind'] == 'evidence')
        self.assertEqual(card['figure_id'],fig['figure_id'])
        revision = next(d['revision'] for d in material['documents'] if d['id'] == doc)
        page_key = f'page:{doc}:1:{revision}'
        self.body['rows'][0]['references'] = [card['key'],fig['key'],page_key]
        self.body['rows'][1]['references'] = [card['key']]
        self.assertEqual(self.client.put(self.url,json=self.body).status_code, 409)
        self.body['rows'][1]['references'] = []
        saved = self.client.put(self.url,json=self.body)
        self.assertEqual(saved.status_code, 200,saved.text)
        self.assertEqual(saved.json()['rows'][0]['sources'][0]['text'], 'Actual recorded quotation')
        refurl = self.base+'/material/'+self.a+'/source'
        for key in self.body['rows'][0]['references']:
            nav = self.client.get(refurl,params={'key':key})
            self.assertEqual(nav.status_code, 200,nav.text)
            self.assertEqual(nav.json()['document_id'],doc)
            self.assertEqual(nav.json()['metadata_revision'],revision)
        self.client.patch(paper+'/evidence/'+created.json()['id'],json={'claim':'Changed judgment'})
        self.assertEqual(self.client.get(refurl,params={'key':card['key']}).status_code,409)
        self.assertEqual(self.client.get(self.url).json()['rows'][0]['sources'][0]['label'],'Reader judgment')
        (main.paper_dir(self.a)/'supplements'/(doc+'.pdf')).write_bytes(fixture_pdf('Figure 2','external replacement'))
        self.assertEqual(self.client.get(refurl,params={'key':page_key}).status_code,409)

    def test_removed_paper_preserves_old_notes_and_corrupt_record_survives(self):
        self.assertEqual(self.client.put(self.url,json=self.body).status_code,200)
        changed = self.client.put('/api/project-libraries/'+self.library,
                                  json={'revision':1,'name':'Cohorts','remove':[self.b]})
        self.assertEqual(changed.status_code,200)
        self.assertEqual(self.client.get(self.base+'/material/'+self.b).status_code,404)
        update = self.client.put(self.url,json=dict(self.body, revision=1, question='Revised question'))
        self.assertEqual(update.status_code,200,update.text)
        self.assertEqual(update.json()['rows'][1]['paper_id'], self.b)
        self.body['rows'][1]['references'] = ['forged']
        self.assertEqual(self.client.put(self.url,json=dict(self.body,revision=2)).status_code,409)
        path = self.data/'comparisons'/self.library/('c'*32+'.json')
        good = path.read_text('utf-8')
        path.write_text(json.dumps({'future_version':10}))
        self.assertEqual(self.client.get(self.url).status_code,409)
        self.assertEqual(self.client.put(self.url,json=dict(self.body,revision=2)).status_code,409)
        self.assertNotEqual(path.read_text(), good)
        self.assertEqual(json.loads(path.read_text()), {'future_version':10})

    def test_retained_crop_and_its_evidence_cannot_cite_the_replacement_pdf(self):
        paper = '/api/papers/' + self.a
        directory = main.paper_dir(self.a)
        crop = self.client.post(paper + '/figures/crop', json={'page':1, 'bbox':[50, 200, 230, 340], 'label':'Panel B'})
        self.assertEqual(crop.status_code, 200, crop.text)
        fid = crop.json()['id']
        card = self.client.post(paper + '/evidence', json={'page':1, 'figure_id':fid, 'claim':'Panel B comparison'})
        self.assertEqual(card.status_code, 200, card.text)
        selected = [s for s in self.get_material(self.a)['sources'] if s['figure_id'] == fid]
        self.assertEqual({s['kind'] for s in selected}, {'figure', 'evidence'})
        keys = [s['key'] for s in selected]
        self.body['rows'][0]['references'] = keys
        self.assertEqual(self.client.put(self.url, json=self.body).status_code, 200)
        nav = self.base + '/material/' + self.a + '/source'

        # Re-extraction may retain the same label, page, and ID while changing its rectangle.
        figures = main.load_figures(directory)
        next(f for f in figures if f['id'] == fid)['bbox'] = [55, 210, 200, 305]
        main.save_figures(directory, figures)
        for key in keys:
            self.assertEqual(self.client.get(nav, params={'key':key}).status_code, 409)

        replacement = self.client.post(paper + '/file', files={'file':('changed.pdf',fixture_pdf(marker='New PDF'))})
        self.assertEqual(replacement.status_code, 200, replacement.text)
        # Even a legacy card stamped with the new document revision cannot validate its historic crop.
        cards = main.load_evidence(directory)
        cards[0]['source_revision'] = main.read_meta(directory)['document_revisions']['main']
        main.atomic_json(directory/'evidence.json', cards)
        offered = [s for s in self.get_material(self.a)['sources'] if s['figure_id'] == fid]
        self.assertEqual({s['kind'] for s in offered}, {'evidence','figure'})
        for stale in offered:
            self.assertFalse(stale['available'])
            self.assertEqual(self.client.get(nav, params={'key':stale['key']}).status_code, 409)
        self.assertEqual(self.client.get(self.url).json()['rows'][0]['sources'], selected)


if __name__ == '__main__':
    unittest.main()
