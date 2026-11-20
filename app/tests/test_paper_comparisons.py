"""End-to-end project comparisons, paper-scoped source snapshots and revision conflicts."""
import json
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
