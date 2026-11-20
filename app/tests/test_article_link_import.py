"""A confirmed link import joins its original project and attaches only checked material."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_documents import TestClient, fixture_pdf, main


class LinkImportTests(unittest.TestCase):
    url = 'https://research.example/articles/pilot'
    file_url = 'https://papers.example/content/pilot.pdf'
    title = 'MAIN synthetic paper for source isolation'

    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for field, value in [('DATA_DIR', root), ('PAPERS_DIR', root / 'papers'), ('TRASH_DIR', root / 'trash')]:
            self.enterContext(patch.object(main, field, value))
        self.client = self.enterContext(TestClient(main.app))
        self.project = self.client.post('/api/project-libraries', json={'name': 'Pilot'}).json()['id']
        self.pdf = fixture_pdf()
        self.meta = {'title': self.title, 'doi': '', 'pmid': '', 'url': self.url,
                     '_link': {'article_url': self.url, 'pdf_url': self.file_url, 'inspected': True}}
        self.inspect = self.enterContext(patch.object(main.article_links, 'inspect_url', AsyncMock(return_value=self.meta)))
        self.download = self.enterContext(patch.object(main.article_links, 'download_pdf', AsyncMock(side_effect=self.checked)))
        self.open_lookup = self.enterContext(patch.object(main.oa, 'obtain', AsyncMock(
            return_value={'outcome': 'not_found', 'message': 'No indexed open PDF'})))
        self.enterContext(patch.object(main.ids, 'fetch_crossref', AsyncMock(
            return_value={'title': self.title, 'doi': '10.1234/pilot'})))

    async def checked(self, *args):
        return {'outcome': 'downloaded', 'message': '正文 PDF 已下载。', 'content': self.pdf,
                'source': {'provider': '文章页面', 'url': self.file_url, 'version': 'unknown'}}

    def preview(self):
        response = self.client.post('/api/import/identifiers/preview', json={'text': self.url, 'library_id': self.project})
        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()
        self.assertIn(preview['rows'][0]['status'], ('new', 'existing'), preview)
        return preview['preview_id']

    def apply(self, token):
        response = self.client.post('/api/import/identifiers/apply', json={'preview_id': token, 'keys': [0]})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['rows'][0]

    def test_page_without_doi_preview_is_read_only_apply_once_and_reuse(self):
        token = self.preview()
        self.assertEqual(main.list_meta(), [])
        self.download.assert_not_awaited()
        row = self.apply(token)
        pid = row['paper_id']
        self.assertEqual(row['fulltext']['outcome'], 'downloaded', row)
        self.assertEqual(main.project_paper_ids(self.project), {pid})
        saved = self.client.get('/api/papers/' + pid).json()
        self.assertEqual((saved['import_url'], saved['title'], saved['source']), (self.url, self.title, 'url'))
        self.assertNotIn('_link', saved)
        self.assertEqual(self.client.get('/api/papers/' + pid + '/file').content, self.pdf)
        self.client.patch('/api/papers/' + pid, json={'notes': 'Keep this note'})
        again = self.apply(token)
        self.assertEqual((again['paper_id'], again['fulltext']['outcome']), (pid, 'already_present'))
        self.assertEqual(len(main.list_meta()), 1)
        self.assertEqual(main.read_meta(main.paper_dir(pid))['notes'], 'Keep this note')
        self.download.assert_awaited_once()
        self.open_lookup.assert_not_awaited()

    def test_unavailable_pdf_keeps_record_and_supports_new_session_retry(self):
        token = self.preview()
        self.download.side_effect = None
        self.download.return_value = {'outcome': 'failed', 'message': 'Temporarily unavailable'}
        row = self.apply(token)
        self.assertEqual(row['status'], 'added')
        self.assertEqual(row['fulltext']['outcome'], 'failed')
        pid = row['paper_id']
        self.assertFalse(main.read_meta(main.paper_dir(pid))['has_file'])
        self.download.side_effect = self.checked
        result = self.client.post('/api/papers/' + pid + '/fetch-fulltext', params={'library_id': self.project})
        self.assertEqual(result.json()['outcome'], 'downloaded', result.text)
        self.assertEqual(len(main.list_meta()), 1)
        self.assertEqual(self.inspect.await_count, 2)

    def test_direct_pdf_matches_local_upload_by_hash(self):
        pid = self.client.post('/api/papers', files={'file': ('original.pdf', self.pdf)}).json()['id']
        self.client.patch('/api/papers/' + pid, json={'notes': 'Original'})
        self.meta['_link'].update(pdf_url=self.url, sha256=hashlib.sha256(self.pdf).hexdigest())
        row = self.apply(self.preview())
        self.assertEqual((row['paper_id'], row['fulltext']['outcome']), (pid, 'already_present'))
        self.assertEqual(main.project_paper_ids(self.project), {pid})
        self.assertEqual(main.read_meta(main.paper_dir(pid))['notes'], 'Original')
        self.download.assert_not_awaited()

    def test_missing_publisher_pdf_uses_index_for_identified_paper(self):
        self.meta['doi'] = '10.1234/pilot'
        self.meta['_link'].pop('pdf_url')
        self.download.side_effect = None
        self.download.return_value = {'outcome': 'not_found', 'message': 'No publisher PDF'}
        self.open_lookup.side_effect = self.checked
        row = self.apply(self.preview())
        self.assertEqual(row['fulltext']['outcome'], 'downloaded', row)
        self.open_lookup.assert_awaited_once()

    def test_deleting_a_confirmed_record_is_respected_when_retrying_same_preview(self):
        token = self.preview()
        pid = self.apply(token)['paper_id']
        self.client.delete('/api/papers/' + pid).raise_for_status()
        self.client.delete('/api/trash/' + pid).raise_for_status()
        self.assertEqual(self.apply(token)['status'], 'changed')
        self.assertEqual(main.list_meta(), [])

    def test_membership_removed_during_download_cannot_attach_or_rejoin(self):
        async def remove_then_return(*args):
            pid, = main.project_paper_ids(self.project)
            project = main.pl.read(main.DATA_DIR, self.project)
            main.pl.edit(main.DATA_DIR, project, main.pl.ProjectEdit(name=project.name, revision=project.revision, remove=[pid]))
            return await self.checked()

        self.download.side_effect = remove_then_return
        token = self.preview()
        row = self.apply(token)
        self.assertEqual(row['fulltext']['outcome'], 'failed')
        self.assertEqual(self.apply(token)['status'], 'changed')
        self.assertFalse(main.read_meta(main.paper_dir(row['paper_id']))['has_file'])
        self.assertEqual(main.project_paper_ids(self.project), set())


if __name__ == '__main__':
    unittest.main()
