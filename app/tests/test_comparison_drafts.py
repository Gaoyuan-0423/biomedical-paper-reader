"""Review-only comparison drafts from synthetic, revision-checked local sources."""
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_documents import fixture_pdf, main, TestClient
from pypdf import PdfReader, PdfWriter
from comparison_drafts import labelled_units, proposed_value, quote_on_page


class ComparisonDraftTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='bioreader-comparison-drafts-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for key, value in [('DATA_DIR', root), ('PAPERS_DIR', root/'papers'), ('TRASH_DIR', root/'trash')]:
            override = patch.object(main, key, value)
            override.start()
            self.addCleanup(override.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.enrolled = 'We enrolled 12 patients in the discovery cohort.'
        self.paired = '8 patients had paired primary and liver metastasis samples.'
        self.single = 'Single-cell analysis included 6 patients from the paired group.'
        self.b_quote = 'Paired patient counts are provided in Supplementary Table S2.'
        self.a = self.upload('paper-a.pdf', self.enrolled, self.paired)
        self.b = self.upload('paper-b.pdf', self.b_quote)
        self.scope = self.client.post('/api/project-libraries', json={'name': 'Pairs', 'paper_ids': [self.a, self.b]}).json()['id']
        self.base = '/api/comparisons/' + self.scope
        self.columns = ['enrolled', 'paired', 'single_cell', 'sites']
        self.query = {'question': 'Which patients were paired?', 'paper_ids': [self.a, self.b], 'columns': self.columns}
        self.url = self.base + '/' + 'c'*32

    def upload(self, name, marker, label='Figure 1'):
        return self.client.post('/api/papers', files={'file': (name, fixture_pdf(label, marker))}).json()['id']

    def card(self, pid, quote, doc='main', status='pending', claim='Reader judgment'):
        result = self.client.post('/api/papers/' + pid + '/evidence',
                                  json={'quote': quote, 'document_id': doc, 'page': 1, 'claim': claim, 'status': status})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()['id']

    def draft(self):
        draft = self.client.post(self.base + '/draft', json=self.query)
        self.assertEqual(draft.status_code, 200, draft.text)
        self.assertEqual(draft.json()['scope'], 'saved_sources')
        return [{g['cohort']: g['cells'] for g in paper['cohorts']} for paper in draft.json()['papers']]

    def test_sources_on_selected_pages_denominators_and_missing_supplement(self):
        resource_url = '/api/papers/' + self.a + '/resources'
        self.card(self.a, self.enrolled, status='verified')
        self.card(self.a, self.paired)
        # A plausible judgment or a copied quote from another paper isn't evidence.
        self.card(self.a, self.single)
        self.card(self.b, self.b_quote)
        self.card(self.b, self.enrolled)
        doc = self.client.post('/api/papers/' + self.a + '/supplements', files={'file': ('counts.pdf', fixture_pdf(marker=self.single))}).json()['supplements'][0]['id']
        field = {'document_id': doc, 'page': 1, 'quote': self.single}
        result = self.client.post(resource_url, json={'title': 'Assayed subset', 'accession': 'GSE98765', 'sample_count': '6 patients', 'field_source_changes': {'sample_count': field}})
        self.assertEqual(result.status_code, 200, result.text)
        self.card(self.a, self.single, doc)
        self.card(self.a, self.enrolled, status='rejected')
        a, b = self.draft()
        self.assertEqual(a['discovery cohort']['enrolled']['candidates'][0]['value'], '12 patients')
        self.assertEqual(len(a['discovery cohort']['enrolled']['candidates']), 1)
        self.assertEqual(a['']['enrolled']['status'], 'not_found')
        self.assertEqual([c['value'] for c in a['']['paired']['candidates']], ['8 patients'])
        candidates = a['']['single_cell']['candidates']
        self.assertEqual([c['value'] for c in candidates], ['6 patients', '6 patients'])
        self.assertEqual({c['source']['kind'] for c in candidates}, {'evidence', 'field'})
        for c in candidates:
            src = c['source']
            self.assertEqual((src['text'], src['document_name'], src['page']), (self.single, 'counts.pdf', 1))
            nav = self.client.get(self.base + '/material/' + self.a + '/source', params={'key': src['key']})
            self.assertEqual(nav.json()['document_id'], doc)
        self.assertEqual(b['']['paired']['status'], 'missing_supplement')
        self.assertEqual(b['']['paired']['reason_source']['text'], self.b_quote)
        self.assertEqual(b['']['single_cell']['status'], 'not_found')
        self.assertEqual(b['']['sites']['status'], 'not_found')
        self.assertEqual(b['']['paired']['candidates'], [])
        self.assertEqual(self.client.get(self.base).json(), [])
        self.assertEqual(b['']['paired']['supplement_label'], 'Supplementary Table S2')
        self.client.post('/api/papers/' + self.b + '/supplements', files={'file': ('FigS1.pdf', fixture_pdf())})
        _, b = self.draft()
        self.assertEqual(b['']['paired']['status'], 'missing_supplement')
        self.client.post('/api/papers/' + self.b + '/supplements', files={'file': ('supp.pdf', fixture_pdf(marker='Combined'))})
        _, b = self.draft()
        self.assertEqual(b['']['paired']['status'], 'check_supplement')

    def test_review_snapshots_older_client_and_invalid_sources(self):
        identifier = self.card(self.a, self.enrolled)
        candidate = self.draft()[0]['discovery cohort']['enrolled']['candidates'][0]
        key = candidate['source']['key']
        cell = {'column': 'enrolled', 'cohort': 'discovery cohort', 'value': '12（人工复核）', 'source_key': key}
        rows = [{'paper_id': self.a, 'note': 'Manual notes', 'cells': [cell]}, {'paper_id': self.b}]
        data = {'revision': 0, 'question': 'paired?', 'columns': ['enrolled'], 'rows': rows}
        for bad in [dict(cell, cohort='validation cohort'), dict(cell, column='paired'), dict(cell, source_key='forged')]:
            wrong = dict(data, rows=[dict(rows[0], cells=[bad]), rows[1]])
            self.assertEqual(self.client.put(self.url, json=wrong).status_code, 409)
        self.assertEqual(self.client.put(self.url, json=dict(data, rows=[dict(rows[0], cells=[cell, cell]), rows[1]])).status_code, 422)
        saved = self.client.put(self.url, json=data)
        self.assertEqual(saved.status_code, 200, saved.text)
        saved = saved.json()
        self.assertEqual(self.client.put(self.url, json=data).json(), saved)
        self.assertEqual(saved['version'], 2)
        snapshot = saved['rows'][0]['cells'][0]
        self.assertEqual((snapshot['value'], snapshot['source']['text']), ('12（人工复核）', self.enrolled))
        self.assertEqual(saved['rows'][0]['note'], 'Manual notes')
        self.assertEqual(saved['rows'][1]['cells'], [])
        self.client.delete('/api/papers/' + self.a + '/evidence/' + identifier)
        stale = dict(data, revision=1, rows=[dict(rows[0], cells=[dict(cell, value='changed')]), rows[1]])
        self.assertEqual(self.client.put(self.url, json=stale).status_code, 409)
        legacy = {'revision': 1, 'question': 'updated?', 'rows': [{'paper_id': self.a, 'note': 'Revised'}, rows[1]]}
        retained = self.client.put(self.url, json=legacy)
        self.assertEqual(retained.status_code, 200, retained.text)
        self.assertEqual(retained.json()['columns'], ['enrolled'])
        self.assertEqual(retained.json()['rows'][0]['cells'], [snapshot])
        self.assertEqual(self.client.put(self.url, json=dict(data, revision=1)).status_code, 409)
        self.assertEqual(self.client.get(self.url).json()['question'], 'updated?')

    def test_real_revision_nonmembers_and_request_bounds(self):
        self.card(self.a, self.enrolled)
        key = self.draft()[0]['discovery cohort']['enrolled']['candidates'][0]['source']['key']
        (main.paper_dir(self.a) / 'paper.pdf').write_bytes(fixture_pdf(marker='Changed PDF'))
        self.assertEqual(self.draft()[0]['']['enrolled']['status'], 'not_found')
        body = {'revision': 0, 'question': 'paired?', 'rows': [{'paper_id': self.b}, {'paper_id': self.a, 'cells': [
            {'source_key': key, 'value': '12 patients', 'column': 'enrolled', 'cohort': 'discovery cohort'}]}]}
        self.assertEqual(self.client.put(self.url, json=body).status_code, 409)
        for patch_data in [{'paper_ids': [self.a]}, {'paper_ids': [self.a, self.a]}, {'columns': ['paired', 'paired']},
                           {'columns': []}, {'columns': ['figure']}, {'question': '  '}]:
            self.assertEqual(self.client.post(self.base + '/draft', json=dict(self.query, **patch_data)).status_code, 422)
        outsider = self.upload('outside.pdf', 'Outside')
        self.assertEqual(self.client.post(self.base + '/draft', json=dict(self.query, paper_ids=[self.a, outsider])).status_code, 409)

    def test_numbered_spreadsheet_and_question_specific_pair_sites(self):
        self.card(self.a, self.paired)
        self.card(self.b, self.b_quote)
        self.query['question'] = '乳腺癌原发灶和肝转移灶配对的是哪些研究？'
        a, b = self.draft()
        self.assertEqual(a['']['paired']['candidates'][0]['value'], '8 patients')
        self.query['question'] = 'How many patients had paired primary and lung metastasis samples?'
        a, _ = self.draft()
        self.assertEqual(a['']['paired']['status'], 'not_found')
        directory = main.paper_dir(self.b)
        metadata = main.read_meta(directory)
        # Use the same content-addressed local layout as a downloaded table.
        digest = hashlib.sha256(b'synthetic attachment').hexdigest()
        folder = directory / 'supplement-files'
        folder.mkdir()
        (folder / digest).write_bytes(b'synthetic attachment')
        entry = {'id': 'test', 'filename': 'journal_attachment.xlsx', 'source_label': 'Supplementary Table S3', 'sha256': digest}
        metadata['supplement_files'] = [entry]
        main.write_meta(directory, metadata)
        self.assertEqual(self.draft()[1]['']['paired']['status'], 'missing_supplement')
        entry['source_label'] = 'Supplementary Table S2'
        main.write_meta(directory, metadata)
        verified = self.draft()[1]['']['paired']
        self.assertEqual((verified['status'], verified['supplement_label']), ('check_supplement', 'Supplementary Table S2'))

    def test_wrong_page_and_evidence_replaced_during_save(self):
        pdf = PdfWriter()
        for marker in ('Unrelated first page', self.single):
            pdf.add_page(PdfReader(io.BytesIO(fixture_pdf(marker=marker))).pages[0])
        content = io.BytesIO()
        pdf.write(content)
        doc = self.client.post('/api/papers/' + self.a + '/supplements', files={'file': ('two.pdf', content.getvalue())}).json()['supplements'][0]['id']
        self.card(self.a, self.single, doc)
        self.assertEqual(self.draft()[0]['']['single_cell']['status'], 'not_found')
        card = self.client.post('/api/papers/' + self.a + '/evidence', json={
            'page': 2, 'document_id': doc, 'claim': 'Read original', 'quote': self.single,
            'expected_source_revision': hashlib.sha256(content.getvalue()).hexdigest(),
            'selection_bbox': [0.1, 0.1, 0.7, 0.2]}).json()
        candidate = self.draft()[0]['']['single_cell']['candidates'][0]
        self.assertEqual((candidate['source']['page'], candidate['source']['bbox']), (2, [0.1, 0.1, 0.7, 0.2]))
        original = main.comparison_draft_paper

        def concurrent_edit(*args, **kwargs):
            result = original(*args, **kwargs)
            self.client.patch('/api/papers/' + self.a + '/evidence/' + card['id'], json={'claim': 'Updated elsewhere'})
            return result

        cell = {'column': 'single_cell', 'cohort': '', 'value': '6 patients', 'source_key': candidate['source']['key']}
        request = {'revision': 0, 'question': 'Actual analysis?', 'rows': [{'paper_id': self.a, 'cells': [cell]}, {'paper_id': self.b}]}
        with patch.object(main, 'comparison_draft_paper', side_effect=concurrent_edit):
            self.assertEqual(self.client.put(self.url, json=request).status_code, 409)
        self.assertEqual(self.client.get(self.base).json(), [])

    def test_opt_in_pdf_fills_only_missing_cohort_cells_then_checks_selected_block(self):
        self.card(self.a, self.enrolled)
        doc = self.client.post('/api/papers/' + self.a + '/supplements',
                               files={'file': ('extra.pdf', fixture_pdf(marker=self.single))}).json()['supplements'][0]['id']
        base = self.draft()[0]
        self.assertNotIn('', base)
        request = {**self.query, 'include_pdf': True}
        result = self.client.post(self.base + '/draft', json=request).json()
        self.assertEqual(result['scope'], 'saved_and_pdf')
        paper = result['papers'][0]
        self.assertEqual({(c['document_id'], c['examined']) for c in paper['pdf_coverage']}, {('main', 1), (doc, 1)})
        cohorts = {c['cohort']: c['cells'] for c in paper['cohorts']}
        self.assertEqual({c['source']['kind'] for c in cohorts['discovery cohort']['enrolled']['candidates']}, {'evidence'})
        candidate = cohorts['']['single_cell']['candidates'][0]
        source = candidate['source']
        self.assertEqual((candidate['value'], source['kind'], source['document_id'], source['page']), ('6 patients','pdf',doc,1))
        self.assertEqual(source['text'], self.single)
        missing = result['papers'][1]['cohorts'][0]['cells']['paired']
        self.assertEqual((missing['status'], missing['reason_source']['kind']), ('missing_supplement','pdf'))
        url = self.base + '/material/' + self.a + '/source'
        self.assertEqual(self.client.get(url,params={'key':source['key']}).json()['text'], self.single)
        self.assertEqual(self.client.get(self.base+'/material/'+self.b+'/source',params={'key':source['key']}).status_code,409)
        self.assertEqual(self.client.get(self.base).json(),[])
        cell = {'column':'single_cell', 'cohort':'', 'value':'6（复核）', 'source_key':source['key']}
        rows = [{'paper_id':self.a,'note':'Handwritten','cells':[cell]}, {'paper_id':self.b}]
        saved = self.client.put(self.url, json={'revision':0,'question':request['question'],'rows':rows})
        self.assertEqual(saved.status_code,200,saved.text)
        snapshot = saved.json()['rows'][0]['cells'][0]
        self.assertEqual((snapshot['source']['text'],snapshot['value']), (self.single,'6（复核）'))
        original = main.paper_dir(self.a)/'supplements'/(doc+'.pdf')
        original.write_bytes(fixture_pdf(marker=self.single + ' New revision'))
        self.assertEqual(self.client.get(url,params={'key':source['key']}).status_code,409)
        changed = {**rows[0], 'cells':[dict(cell,value='edited again')]}
        self.assertEqual(self.client.put(self.url,json={'revision':1,'question':request['question'],'rows':[changed,rows[1]]}).status_code,409)
        self.assertEqual(self.client.get(self.url).json()['rows'][0]['cells'][0],snapshot)

    def test_pdf_budget_and_image_only_pages_show_what_was_unread(self):
        pdf = PdfWriter()
        pdf.add_blank_page(width=600,height=800)
        pdf.add_page(PdfReader(io.BytesIO(fixture_pdf(marker=self.single))).pages[0])
        blob = io.BytesIO()
        pdf.write(blob)
        doc = self.client.post('/api/papers/'+self.a+'/supplements',files={'file':('mixed.pdf',blob.getvalue())}).json()['supplements'][0]['id']
        with patch.object(main.comparison_pdf,'PER_DRAFT_PAGES',4):
            result = self.client.post(self.base+'/draft',json={**self.query,'include_pdf':True}).json()
        paper = result['papers'][0]
        coverage = next(c for c in paper['pdf_coverage'] if c['document_id'] == doc)
        self.assertEqual((coverage['examined'],coverage['total'],coverage['no_text']), (1,2,[1]))
        self.assertTrue(all(g['cells']['single_cell']['status']=='not_found' for g in paper['cohorts']))

    def test_ambiguous_counts_no_sample_conversion_and_positive_sites(self):
        source = {'kind': 'evidence'}
        for clause, col in [('20 samples were enrolled.', 'enrolled'), ('40 primary-metastasis pairs.', 'paired'),
                            ('We recruited 12 patients and sequenced 3 samples.', 'single_cell'),
                            ('Among 20 patients, paired samples were unavailable.', 'paired'),
                            ('Paired primary and liver biopsy samples were unavailable for 9 patients.', 'paired'),
                            ('Patients without paired tissue samples accounted for 9 patients.', 'paired'),
                            ('Single-cell sequencing failed in 8 patients.', 'single_cell'),
                            ('Samples from 12 patients failed quality control before single-cell sequencing.', 'single_cell'),
                            ('Samples from 17 patients were stored for future single-cell sequencing.', 'single_cell'),
                            ('We enrolled 8 patients per group.', 'enrolled'),
                            ('Paired technical replicate samples were obtained from 10 patients.', 'paired'),
                            ('Paired sequencing of DNA and RNA samples was performed for 14 patients.', 'paired'),
                            ('Sex-matched samples from 18 patients were compared.', 'paired'),
                            ('Age-matched control tissue was obtained from 9 patients.', 'paired'),
                            ('Paired samples came from 8 different donors.', 'paired'),
                            ('8例患者提供了年龄匹配的配对样本', 'paired'),
                            ('We enrolled at least 8 patients.', 'enrolled'),
                            ('We enrolled 1.8 patients.', 'enrolled'),
                            ('In 2020, 8 biopsies from patients were enrolled.', 'enrolled'),
                            ('We enrolled 20 specimens from 8 patients.', 'enrolled'),
                            ('10 patients were excluded from single-cell analysis.', 'single_cell'),
                            ('A subset of 8 patients underwent single-cell sequencing.', 'single_cell'),
                            ('Single-cell analysis included 6 patients from the paired group.', 'paired'),
                            ('Single-cell analysis included 6 patients from the paired group.', 'enrolled')]:
            self.assertIsNone(proposed_value(col, clause, source), clause)
        for col, clause, expected in [('enrolled', '共纳入12名患者', '12名患者'),
                                      ('paired', '8例患者有配对的原发灶和肝转移灶样本', '8例患者'),
                                      ('single_cell', '6名患者实际进入单细胞分析', '6名患者')]:
            self.assertEqual(proposed_value(col, clause, source), expected)
        self.assertEqual(proposed_value('sites', 'We obtained breast and liver samples.', source), 'We obtained breast and liver samples.')
        self.assertEqual(proposed_value('sites', 'Patient tissue was skin.', {'field': 'tissue', 'field_value': 'skin'}), 'skin')
        self.assertFalse(quote_on_page('12 patients were enrolled', '112 patients were enrolled'))
        self.assertTrue(quote_on_page('12名患者', '共12名患者参与'))
        cohorts = list(labelled_units('We enrolled 8 patients in the discovery cohort; we enrolled 9 patients in the validation cohort.'))
        self.assertEqual([c for c, _ in cohorts], ['discovery cohort', 'validation cohort'])
        cohorts = list(labelled_units('We enrolled 20 patients, 8 patients had paired samples in the validation cohort.'))
        self.assertEqual([c for c, _ in cohorts], ['', 'validation cohort'])
        self.assertEqual(list(labelled_units('Were 20 patients enrolled?')), [])
        [(cohort, clause)] = labelled_units('In cohort 1, we enrolled 10,200 patients.')
        self.assertEqual((cohort, proposed_value('enrolled', clause, source)), ('cohort 1', '10,200 patients'))


if __name__ == '__main__':
    unittest.main()
