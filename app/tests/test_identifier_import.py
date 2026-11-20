"""Mixed DOI/PMID previews, existing project memberships and independent retries."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_documents import TestClient, main

ids = main.ids
DOI = '10.1234/study(3)'
PMID = '12345'


def record(doi=DOI, pmid=''):
    return dict(title='Trial of synthetic case numbers', doi=doi, pmid=pmid, authors='A. Author',
                year='2026', journal='Synthetic journal', abstract='Sample abstract', url='https://example.org/fake')


class IdentifierImportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for name, val in [('PAPERS_DIR',root/'papers'),('TRASH_DIR',root/'trash'),('DATA_DIR',root)]:
            p = patch.object(main,name,val); p.start(); self.addCleanup(p.stop)
        self.client = TestClient(main.app)
        self.lookup_doi = self.start(patch.object(ids,'fetch_crossref',AsyncMock(return_value=record())))
        self.lookup_pmid = self.start(patch.object(ids,'fetch_pubmed',AsyncMock(return_value=record(pmid=PMID))))
        self.project = self.client.post('/api/project-libraries', json={'name':'Example'}).json()

    def start(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def preview(self, text, **extra):
        r = self.client.post('/api/import/identifiers/preview', json={'text': text, 'library_id':self.project['id'], **extra})
        self.assertEqual(r.status_code,200,r.text)
        return r.json()

    def apply(self, preview, keys):
        r = self.client.post('/api/import/identifiers/apply',json={'preview_id':preview['preview_id'],'keys':keys})
        self.assertEqual(r.status_code,200,r.text)
        return r.json()['rows']

    def test_mixed_preview_apply_existing_and_trash_protection(self):
        pasted = 'https://journals.example/article/10.1234/study(3)?src=email\nPMID: 12345 bad https://pubmed.ncbi.nlm.nih.gov/12345/'
        # Public links now also look up the article page and its main PDF.
        self.start(patch.object(main.article_links, 'inspect_url', AsyncMock(side_effect=main.article_links.ArticleLinkError('Offline fixture'))))
        self.start(patch.object(main.oa, 'obtain', AsyncMock(return_value={'outcome':'not_found', 'message':'Fixture without full text'})))
        pre = self.preview(pasted)
        self.assertEqual([r['status'] for r in pre['rows']],['new','duplicate','invalid','duplicate'])
        self.assertEqual(pre['rows'][0]['pmid'],PMID)
        self.assertEqual(main.list_meta(),[])
        self.lookup_doi.assert_awaited_once_with(DOI)
        self.lookup_pmid.assert_awaited_once_with(PMID)
        applied = self.apply(pre,[0])[0]
        pid = applied['paper_id']
        self.assertEqual(applied['status'],'added')
        self.assertEqual(main.project_paper_ids(self.project['id']),{pid})
        self.assertEqual(main.read_meta(main.paper_dir(pid))['pmid'],PMID)
        self.assertEqual(self.apply(pre,[0])[0]['status'],'existing')
        again = self.preview(PMID)
        self.assertEqual(again['rows'][0]['status'],'existing')
        self.assertTrue(again['rows'][0]['in_project'])
        self.client.delete('/api/papers/'+pid)
        self.assertEqual(self.apply(again,[0])[0]['status'],'trashed')
        self.assertEqual(self.preview(PMID)['rows'][0]['status'],'trashed')
        self.assertEqual(len(main.list_meta()),0)
        self.client.delete('/api/trash/'+pid)
        self.assertEqual(self.apply(again,[0])[0]['status'],'changed')
        self.assertEqual(len(main.list_meta()),0)

    def test_retry_lookup_uses_last_success_and_apply_never_accepts_arbitrary_keys(self):
        self.lookup_pmid.side_effect = ids.LookupError_('temporary')
        pre = self.preview(DOI+' '+PMID)
        self.assertEqual([r['status'] for r in pre['rows']],['new','failed'])
        bad = self.client.post('/api/import/identifiers/apply',json={'preview_id':pre['preview_id'],'keys':[1]})
        self.assertEqual(bad.status_code,422)
        self.lookup_pmid.side_effect = None
        retry = self.preview(DOI+' '+PMID, retry_preview_id=pre['preview_id'])
        self.assertEqual(retry['rows'][1]['status'],'duplicate')
        self.assertEqual(self.lookup_doi.await_count,1)
        self.assertEqual(self.lookup_pmid.await_count,2)
        self.assertEqual(self.apply(retry,[0])[0]['status'],'added')

    def test_existing_public_record_joins_project_without_overwriting_notes_and_partial_join_is_retryable(self):
        r = self.client.post('/api/import/identifiers/preview',json={'text':PMID}).json()
        paper_id = self.apply(r,[0])[0]['paper_id']
        directory = main.paper_dir(paper_id)
        saved = main.read_meta(directory); saved['notes']='Handwritten note'; main.write_meta(directory,saved)
        pre = self.preview(DOI)
        self.assertEqual(pre['rows'][0]['status'],'existing'); self.assertFalse(pre['rows'][0]['in_project'])
        with patch.object(main.pl, 'add_paper', side_effect=OSError('disk')):
            self.assertEqual(self.apply(pre,[0])[0]['status'],'join_failed')
        self.assertEqual(self.apply(pre,[0])[0]['status'],'existing')
        self.assertEqual(main.read_meta(directory)['notes'],'Handwritten note')
        self.assertEqual(main.project_paper_ids(self.project['id']),{paper_id})

    def test_pmid_only_and_metadata_identity_checks(self):
        self.lookup_pmid.return_value = record(doi='',pmid=PMID)
        pre = self.preview('PMID: '+PMID)
        pid = self.apply(pre,[0])[0]['paper_id']
        self.assertEqual(main.read_meta(main.paper_dir(pid))['source'],'pmid')
        self.lookup_doi.return_value = record(doi='10.1234/other')
        self.assertEqual(self.preview(DOI)['rows'][0]['status'],'failed')

    def test_pubmed_response_source_validation(self):
        import asyncio
        good = {'id':PMID,'source':'MED','title':'Example title', 'doi':DOI.upper(), 'authorString':'A',
                'pubYear':'2025','journalInfo':{'journal':{'title':'Example journal'}},'abstractText':'<p>Example text</p>'}
        # Exercise the real provider parser while all network I/O remains mocked.
        with patch.object(ids, '_get_json', AsyncMock(return_value={'resultList':{'result':[good]}})) as request:
            actual = asyncio.run(REAL_PUBMED(PMID))
            self.assertEqual((actual['pmid'],actual['doi'],actual['abstract']),(PMID,DOI,'Example text'))
            self.assertIn('SRC%3AMED',request.await_args.args[0]); self.assertEqual(request.await_args.args[1],ids.PMC_HOST)
        with patch.object(ids, '_get_json', AsyncMock(return_value={'resultList':{'result':[{**good,'source':'PPR'}]}})):
            with self.assertRaises(ids.LookupError_):
                asyncio.run(REAL_PUBMED(PMID))


REAL_PUBMED = ids.fetch_pubmed

if __name__ == '__main__':
    unittest.main()
