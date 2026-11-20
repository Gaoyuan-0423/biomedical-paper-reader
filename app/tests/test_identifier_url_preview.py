"""Mixed article/PDF preview checks. All network lookups are stubbed."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_documents import main

ids = main.ids
DOI = '10.1234/trial'
ARTICLE = 'https://papers.example.org/papers/trial?query=a,b&year=2026'
PDF = 'https://files.example.org/trial.pdf'
DIGEST = 'f' * 64


class IdentifierUrlPreviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.page = self.patch(ids.article_links, 'inspect_url', AsyncMock())
        self.crossref = self.patch(ids, 'fetch_crossref', AsyncMock(return_value={
            'title': 'Canonical title', 'doi': DOI, 'url': 'https://doi.org/' + DOI, 'journal': 'Journal'}))
        self.pubmed = self.patch(ids, 'fetch_pubmed', AsyncMock(return_value={
            'title': 'Canonical title', 'doi': DOI, 'pmid': '12345'}))

    def patch(self, obj, key, value):
        patcher = patch.object(obj, key, value)
        self.addCleanup(patcher.stop)
        return patcher.start()

    async def preview(self, text, index=None, members=None):
        return await ids.preview(ids.PreviewIn(text=text), index or {}, members or set(), self.root)

    async def test_link_and_identifier_coalesce_in_both_orders(self):
        self.page.return_value = {'title': 'Page title', 'doi': DOI, 'content': b'not part of a preview',
                                  '_link': {'article_url': ARTICLE, 'pdf_url': PDF, 'inspected': True}}
        for tokens in ((DOI, ARTICLE, 'PMID: 12345'), (ARTICLE, 'PMID: 12345', DOI)):
            rows = await self.preview(' '.join(tokens))
            self.assertEqual([r['status'] for r in rows], ['new', 'duplicate', 'duplicate'])
            row = rows[0]
            self.assertTrue(row['is_link'])
            self.assertEqual((row['title'], row['doi'], row['pmid']), ('Canonical title', DOI, '12345'))
            record = row['record']
            self.assertEqual(record['import_url'], ARTICLE)
            self.assertEqual(record['_link']['pdf_url'], PDF)
            self.assertTrue(record['_link']['inspected'])
            self.assertIn('Canonical title', json.dumps(rows))
            public = ids.remember(self.root, 'all', rows)['rows'][0]
            self.assertNotIn('record', public)
            self.assertTrue(public['is_link'])
        self.assertEqual(self.crossref.await_count, 2)
        self.assertEqual(self.page.await_count, 2)
        self.page.assert_any_await(ARTICLE)

    async def test_doi_path_fallback_and_resolver_link_preserve_import_origin(self):
        self.page.side_effect = ids.article_links.ArticleLinkError('Page temporarily unavailable')
        for link in ('https://journal.example.org/articles/' + DOI, 'https://doi.org/' + DOI,
                     'https://pubmed.ncbi.nlm.nih.gov/12345/'):
            row, = await self.preview(link)
            self.assertEqual((row['status'], row['doi'], row['is_link']), ('new', DOI, True))
            self.assertEqual(row['record']['_link']['article_url'], link)
            self.assertFalse(row['record']['_link'].get('inspected', False))
        self.assertEqual(self.page.await_count, 1)
        direct, = await self.preview(DOI)
        self.assertFalse(direct['is_link'])
        self.assertNotIn('_link', direct['record'])

    async def test_same_doi_less_pdf_from_two_urls_matches_main_hash_and_trash(self):
        second = 'https://files.example.org/copies/trial.pdf'
        async def inspect(url):
            return {'title': 'Trial PDF', '_link': {'article_url': url, 'pdf_url': url, 'sha256': DIGEST,
                                                   'inspected': True}}
        self.page.side_effect = inspect
        paper = {'id': 'a'*32, 'title': 'Existing', 'has_file': True, 'document_revisions': {'main': DIGEST}}
        for active, trash, expected in (([paper], [], 'existing'), ([], [paper], 'trashed')):
            rows = await self.preview(PDF + ' ' + second, ids.library_index(active, trash), {paper['id']})
            self.assertEqual([r['status'] for r in rows], [expected, 'duplicate'])
            self.assertTrue(rows[0]['has_file'])
            self.assertEqual(rows[0]['paper_id'], paper['id'])
        self.assertNotIn(('sha256', DIGEST), ids.aliases({**paper, 'document_revisions': {'main':'1'*64}, 'import_pdf_sha256':DIGEST}))
        self.assertIn(('sha256', DIGEST), ids.aliases({'has_file':False, 'import_pdf_sha256':DIGEST}))

    async def test_unavailable_unidentified_url_stays_retryable_and_invalid_url_is_excluded(self):
        self.page.side_effect = ids.article_links.ArticleLinkError('Page unavailable')
        rows = await self.preview(ARTICLE + ' https://localhost/x bad')
        self.assertEqual([r['status'] for r in rows], ['failed', 'invalid', 'invalid'])
        self.assertEqual(rows[0]['reason'], 'Page unavailable')
        self.crossref.assert_not_awaited()

    async def test_same_original_url_reuses_local_entry_without_first_network_read(self):
        old = {'id': 'b'*32, 'title': 'Existing', 'import_url': ARTICLE, 'has_file': False}
        row, = await self.preview(ARTICLE, ids.library_index([old], []), {old['id']})
        self.assertEqual((row['status'], row['in_project'], row['has_file']), ('existing', True, False))
        self.page.assert_not_awaited()

    async def test_stale_redirect_metadata_conflicts_never_fall_back_to_doi(self):
        link = 'https://journal.example.org/doi/' + DOI
        self.page.return_value = {'title': 'Unrelated work', 'doi': '10.1234/another-study',
                                  '_link': {'article_url': link, 'inspected': True}}
        row, = await self.preview(link)
        self.assertEqual(row['status'], 'failed')
        self.crossref.assert_not_awaited()
        self.page.side_effect = ids.article_links.ArticleIdentityError('Inconsistent identifiers from origin')
        row, = await self.preview(link)
        self.assertEqual((row['status'], row['reason']), ('failed', 'Inconsistent identifiers from origin'))
        self.crossref.assert_not_awaited()

    async def test_pubmed_crosswalk_cannot_override_doi_in_the_pasted_link(self):
        link = 'https://journal.example.org/doi/' + DOI
        self.page.return_value = {'title':'Article', 'pmid':'12345', '_link':{'article_url':link}}
        self.pubmed.return_value = {'title':'Article', 'pmid':'12345', 'doi':'10.1234/other'}
        row, = await self.preview(link)
        self.assertEqual(row['status'], 'failed')

    async def test_publisher_path_with_view_suffix_can_match_article_doi(self):
        link = 'https://journal.example.org/articles/' + DOI + '/full'
        self.page.return_value = {'title': 'Article', 'doi': DOI, '_link': {'article_url': link, 'inspected': True}}
        row, = await self.preview(link)
        self.assertEqual((row['status'], row['doi']), ('new', DOI))
        self.crossref.assert_awaited_once_with(DOI)

    async def test_combined_row_rechecks_the_external_aliases_from_both_links(self):
        other = 'https://alternate.example.org/article'
        def inspect(url):
            hint = {'article_url':url, 'inspected':True}
            if url == other:
                hint['pdf_url'] = PDF
            return {'title':'Article', 'doi':DOI, '_link':hint}
        self.page.side_effect = inspect
        winner, duplicate = await self.preview(ARTICLE + ' ' + other)
        self.assertEqual((winner['status'], duplicate['status']), ('new','duplicate'))
        self.assertEqual(winner['record']['import_url'],other)
        # A concurrent DOI-less import was made using only the first URL.
        snapshot = ids.library_index([{'id':'c'*32,'title':'Article','import_url':ARTICLE}], [])
        status, matched = ids.local_match(snapshot, winner['record'])
        self.assertEqual((status,matched['id']), ('existing','c'*32))
        snapshot = ids.library_index([{'id':'c'*32,'import_url':ARTICLE},{'id':'d'*32,'doi':DOI}], [])
        self.assertEqual(ids.local_match(snapshot, winner['record'])[0], 'conflict')

    def test_active_preview_survives_slow_queue_but_idle_token_expires(self):
        rows = [{'key':i, 'status':'new'} for i in range(50)]
        with patch.object(ids.time,'monotonic') as clock:
            clock.return_value = 1200
            token = ids.remember(self.root, 'all', rows)['preview_id']
            for i in range(50):
                clock.return_value += 119
                body = ids.ApplyIn(preview_id=token, keys=[i])
                self.assertEqual(ids.selection(self.root,body), ('all',[rows[i]]))
            clock.return_value += ids.PREVIEW_TTL + 1
            with self.assertRaises(ids.HTTPException) as caught:
                ids.selection(self.root,body)
            self.assertEqual(caught.exception.status_code, 409)
