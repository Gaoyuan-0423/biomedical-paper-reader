"""Open publication discovery, PDF identity validation, and attachment to an existing record."""
import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from test_documents import fixture_pdf, main, TestClient
import oa_fulltext as oa


TITLE = 'MAIN synthetic paper for source isolation'
DOI = '10.1234/test-paper'
SOURCE = {'provider': 'PubMed Central', 'url': 'https://europepmc.org/articles/PMC42/pdf', 'version': 'published'}


def download():
    return {'outcome': 'downloaded', 'message': '已下载', 'source': SOURCE.copy(), 'content': fixture_pdf()}


class LookupTests(unittest.TestCase):
    def test_checks_title_primary_doi_and_supplement(self):
        self.assertIsNone(oa.verify_pdf(fixture_pdf(), {'title': TITLE, 'doi': DOI}))
        self.assertIsNone(oa.verify_pdf(fixture_pdf(marker=DOI), {'doi': DOI}))
        for pdf in (fixture_pdf(marker='Unrelated'), fixture_pdf(marker='Supporting information'),
                    fixture_pdf(marker='10.1234/another-paper')):
            with self.assertRaises(oa.ReviewNeeded):
                oa.verify_pdf(pdf, {'title': TITLE, 'doi': DOI})
        with self.assertRaises(oa.SourceError):
            oa.verify_pdf(b'<html>Publication landing page</html>', {'title': TITLE})

    def test_publisher_tags_redirect_and_size_limit(self):
        def respond(request):
            if request.url.host == 'doi.org':
                return httpx.Response(302, headers={'Location': 'https://www.nature.com/articles/fixture'})
            return httpx.Response(200, text='<meta name="citation_pdf_url" content="fixture.pdf">'
                                            '<meta name="citation_doi" content="10.1234/test-paper">')
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                sources = await oa.landing(client, {'doi': DOI}, 2048)
                self.assertEqual(sources[0][0]['url'], 'https://www.nature.com/articles/fixture.pdf')
                with self.assertRaises(oa.SourceError):
                    await oa.read_url(client, 'https://doi.org/' + DOI, oa.LANDING_DOMAINS, 10)
        with patch.object(oa, 'assert_safe_external_url') as validate:
            asyncio.run(run())
            self.assertEqual(validate.call_count, 4)

    def test_oa_indexes_validate_work_and_version(self):
        location = {'pdf_url': SOURCE['url'], 'version': 'acceptedVersion', 'is_oa': True}
        async def run(doi):
            transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
                'doi': 'https://doi.org/' + DOI, 'best_oa_location': location, 'open_access': {'is_oa': True}}))
            async with httpx.AsyncClient(transport=transport) as client:
                return await oa.index_candidates(client, {'doi': doi}, 'OpenAlex')
        with patch.object(oa, 'assert_safe_external_url'):
            self.assertEqual(asyncio.run(run(DOI))[0][0]['version'], 'accepted')
            self.assertEqual(asyncio.run(run(DOI + '-other')), [])

    def test_lookup_falls_back_to_pmc_and_checks_checksum(self):
        pdf = fixture_pdf()
        md5 = hashlib.md5(pdf).hexdigest()
        manifest = {'pmcid': 'PMC42', 'version': 1, 'is_manuscript': False,
                    'pdf_url': 's3://pmc-oa-opendata/PMC42.1/PMC42.1.pdf?md5=' + md5}
        with patch.object(oa, 'landing', AsyncMock(side_effect=oa.SourceError('Publisher unavailable'))), \
             patch.object(oa.pmc_materials, 'article_manifests', AsyncMock(return_value=[manifest])), \
             patch.object(oa, 'index_candidates', AsyncMock(return_value=[])), \
             patch.object(oa, 'read_url', AsyncMock(return_value=(SOURCE['url'], pdf))):
            result = asyncio.run(oa.obtain({'pmcid': 'PMC42', 'doi': DOI, 'title': TITLE}, 100000))
            self.assertEqual(result['outcome'], 'downloaded')
            self.assertEqual(result['content'], pdf)
            result = asyncio.run(oa.obtain({'pmcid': 'PMC42', 'doi': DOI, 'title': 'Different publication title'}, 100000))
            self.assertEqual(result['outcome'], 'needs_review')
            manifest['pdf_url'] = manifest['pdf_url'].replace(md5, '0' * 32)
            result = asyncio.run(oa.obtain({'pmcid': 'PMC42', 'doi': DOI, 'title': TITLE}, 100000))
            self.assertEqual(result['outcome'], 'failed')


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='bioreader-oa-tests-')
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, path in [('DATA_DIR', root), ('PAPERS_DIR', root/'papers'), ('TRASH_DIR', root/'trash'),
                           ('SETTINGS_FILE', root/'settings.json')]:
            self.enterContext(patch.object(main, name, path))
        self.client = self.enterContext(TestClient(main.app))
        self.pid, self.d = main.new_paper_dir()
        self.url = '/api/papers/' + self.pid
        meta = main.blank_meta(self.pid)
        meta.update(title=TITLE, doi=DOI, notes='Existing notes', tags=['accepted'])
        main.write_meta(self.d, meta)

    def test_attaches_once_preserves_notes_figures_and_provenance(self):
        supplement = fixture_pdf(label='Supplementary Figure S1', marker='SUPPLEMENT')
        reply = self.client.post(self.url + '/supplements', files={'file': ('supplement.pdf', supplement)})
        self.assertEqual(reply.status_code, 200, reply.text)
        sid = reply.json()['supplements'][0]['id']
        original = self.client.get(self.url + '/figures').json()
        with patch.object(oa, 'obtain', AsyncMock(return_value=download())) as acquire:
            first = self.client.post(self.url + '/fetch-fulltext').json()
            self.assertEqual(first['outcome'], 'downloaded', first)
            self.assertEqual(self.client.post(self.url + '/fetch-fulltext').json()['outcome'], 'already_present')
            acquire.assert_awaited_once()
        meta = self.client.get(self.url).json()
        self.assertEqual((meta['id'], meta['notes'], meta['tags']), (self.pid, 'Existing notes', ['accepted']))
        self.assertEqual(meta['fulltext_source']['sha256'], hashlib.sha256(fixture_pdf()).hexdigest())
        self.assertEqual(meta['fulltext_source']['provider'], SOURCE['provider'])
        self.assertTrue(meta['fulltext_source']['retrieved_at'])
        self.assertEqual(self.client.get(self.url + '/file').content, fixture_pdf())
        self.assertEqual(self.client.get(self.url + '/file', params={'document_id': sid}).content, supplement)
        all_figures = self.client.get(self.url + '/figures').json()
        self.assertGreater(len(all_figures), len(original))
        self.assertTrue(all(item in all_figures for item in original))
        reply = self.client.post(self.url + '/file', files={'file': ('replacement.pdf', fixture_pdf(marker='MANUAL'))})
        self.assertNotIn('fulltext_source', reply.json())

    def test_manual_attachment_and_live_notes_win_a_race(self):
        manual = fixture_pdf(marker='MANUAL')
        async def replace(*_):
            meta = main.ingest_pdf(self.d, manual, 'user.pdf', main.read_meta(self.d))
            meta['notes'] = 'Edited during lookup'
            main.write_meta(self.d, meta)
            return download()
        with patch.object(oa, 'obtain', replace):
            result = self.client.post(self.url + '/fetch-fulltext').json()
        self.assertEqual(result['outcome'], 'already_present')
        self.assertEqual(self.client.get(self.url + '/file').content, manual)
        self.assertEqual(self.client.get(self.url).json()['notes'], 'Edited during lookup')

    def test_live_edits_survive_staging_and_project_removal_rejects_download(self):
        project = main.pl.create(main.DATA_DIR, main.pl.ProjectCreate(name='Study', paper_ids=[self.pid]))
        async def remove(*_):
            project.paper_ids = []
            main.pl.persist(main.DATA_DIR, project)
            return download()
        with patch.object(oa, 'obtain', remove):
            reply = self.client.post(self.url + '/fetch-fulltext', params={'library_id': project.id})
        self.assertEqual(reply.status_code, 409)
        self.assertFalse((self.d / 'paper.pdf').exists())
        with patch.object(oa, 'obtain', AsyncMock(return_value=download())) as lookup:
            reply = self.client.post(self.url + '/fetch-fulltext', params={'library_id': project.id})
            self.assertEqual(reply.status_code, 409)
            lookup.assert_not_awaited()

        parse = main.ingest_pdf
        def edit(*args):
            parsed = parse(*args)
            latest = main.read_meta(self.d)
            latest.update(notes='Edited during parsing', tags=['new'])
            main.write_meta(self.d, latest)
            return parsed
        with patch.object(oa, 'obtain', AsyncMock(return_value=download())), patch.object(main, 'ingest_pdf', edit):
            reply = self.client.post(self.url + '/fetch-fulltext').json()
        self.assertEqual(reply['outcome'], 'downloaded')
        latest = self.client.get(self.url).json()
        self.assertEqual((latest['notes'], latest['tags']), ('Edited during parsing', ['new']))

    def test_write_failure_restores_metadata_and_abstract(self):
        before = (self.d / 'meta.json').read_bytes()
        abstract = self.d / 'text.txt'
        abstract.write_text('Original abstract', encoding='utf8')
        replace = oa.os.replace
        def unavailable(src, dst):
            if Path(src).name == 'meta.json' and Path(src).parent.name.startswith('.fulltext-'):
                raise OSError('simulated disk error')
            return replace(src, dst)
        with patch.object(oa, 'obtain', AsyncMock(return_value=download())), patch.object(oa.os, 'replace', unavailable):
            self.assertEqual(self.client.post(self.url + '/fetch-fulltext').json()['outcome'], 'failed')
        self.assertEqual((self.d / 'meta.json').read_bytes(), before)
        self.assertEqual(abstract.read_text('utf8'), 'Original abstract')
        self.assertFalse((self.d / 'paper.pdf').exists())
        self.assertFalse((self.d / 'figures.json').exists())


if __name__ == '__main__':
    unittest.main()
