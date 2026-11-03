"""GEO supplementation never overwrites paper-use details or fetches arbitrary URLs."""
import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_documents import main, TestClient, fixture_pdf
import geo_metadata as geo
import httpx


def metadata(accession='GSE123'):
    return geo.GeoRecord(accession=accession, source_url=geo.geo_url(accession),
        fetched_at=datetime.now(timezone.utc).isoformat(), geo_sample_count=20,
        samples_total=20, samples_shown=0, overall_design='Synthetic public study').model_dump()


class GeoMetadataTests(unittest.TestCase):
    def test_soft_metadata_and_bounded_sample_examples(self):
        series = '^SERIES = GSE123\r\n!Series_title = Synthetic study\r\n!Series_type = RNA sequencing\r\n'
        series += '\n'.join('!Series_sample_id = GSM' + str(i) for i in range(1, 6))
        series += '\n!Series_platform_id = GPL100\n!Series_overall_design = Two synthetic groups'
        sample = ('^SAMPLE = {id}\n!Sample_title = Sample {id}\n!Sample_organism_ch1 = Homo sapiens\n'
                  '!Sample_source_name_ch1 = tissue sample\n!Sample_characteristics_ch1 = group: control\n'
                  '!Sample_characteristics_ch2 = group: treated\n!Sample_molecule_ch1 = total RNA\n'
                  '!Sample_library_strategy = RNA-Seq\n!Sample_contact_name = Must not collect')
        calls = []
        async def get(client, accession):
            calls.append(accession)
            return series if accession == 'GSE123' else sample.format(id=accession)
        with patch.object(geo, '_get_soft', side_effect=get), patch.object(geo.asyncio, 'sleep', new=AsyncMock()):
            record = asyncio.run(geo.fetch_geo_metadata('gse123'))
        self.assertEqual(calls, ['GSE123', 'GSM1', 'GSM3', 'GSM5'])
        self.assertEqual(record['geo_sample_count'], 5)
        self.assertEqual(record['samples_shown'], 3)
        self.assertEqual(record['samples'][0]['organism'], ['Homo sapiens'])
        self.assertEqual(record['samples'][0]['characteristics'], ['group: control', 'group: treated'])
        self.assertEqual(record['sample_scope'], 'examples')
        self.assertNotIn('Must not collect', json.dumps(record))
        self.assertNotIn('paper_sample_count', record)

    def test_missing_sample_is_partial_not_false_complete(self):
        async def get(client, accession):
            if accession == 'GSE123':
                return '^SERIES = GSE123\n!Series_sample_id = GSM1'
            raise geo.GeoError('unavailable')
        with patch.object(geo, '_get_soft', side_effect=get), patch.object(geo.asyncio, 'sleep', new=AsyncMock()):
            record = asyncio.run(geo.fetch_geo_metadata('GSE123'))
        self.assertEqual(record['geo_sample_count'], 1)
        self.assertEqual(record['samples_shown'], 0)
        self.assertTrue(record['warnings'])
        self.assertFalse(geo.cache_fresh(record))

    def test_absent_or_invalid_sample_list_is_not_reported_as_zero_samples(self):
        for suffix in ['', '\n!Series_sample_id = not-a-sample']:
            with self.subTest(suffix=suffix), patch.object(geo, '_get_soft', new=AsyncMock(return_value='^SERIES = GSE123' + suffix)):
                with self.assertRaisesRegex(geo.GeoError, '无法确认样品总数'):
                    asyncio.run(geo.fetch_geo_metadata('GSE123'))

    def test_inconsistent_cache_counts_sample_scope_and_sources_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix='bioreader-geo-cache-') as temporary:
            directory = Path(temporary)
            sample = {'accession': 'GSM1', 'source_url': geo.geo_url('GSM1')}
            changes = [
                {'samples_shown': 1},
                {'samples_total': 21},
                {'sample_scope': 'all'},
                {'source_url': 'https://example.org/not-geo'},
                {'samples': [sample, sample], 'samples_shown': 2},
                {'samples': [{**sample, 'source_url': 'http://localhost/private'}], 'samples_shown': 1},
                {'samples': [sample], 'samples_shown': 1, 'samples_total': 0, 'geo_sample_count': 0},
            ]
            for change in changes:
                raw = {**metadata(), **change}
                geo.save_geo_cache(directory, {'GSE123': raw})
                before = (directory / 'geo_metadata.json').read_bytes()
                with self.subTest(change=change), self.assertRaises(geo.SidecarError):
                    geo.load_geo_cache(directory)
                self.assertEqual((directory / 'geo_metadata.json').read_bytes(), before)

    def test_reject_non_geo_and_mismatched_content(self):
        for value in ['GSM123', 'https://evil.test', '../GSE123', 'GSE0', 'GSE123&x=y']:
            with self.subTest(value=value), self.assertRaises(geo.GeoError):
                geo.validate_accession(value)
        for content in ['<html>error</html>', '^SERIES = GSE124\n!Series_title = different']:
            with self.assertRaises(geo.GeoError):
                geo.parse_soft(content, 'GSE123', 'SERIES')

    def test_transport_fixed_host_no_redirect_retry_and_payload_cap(self):
        calls = []
        async def run():
            def handle(request):
                calls.append(request)
                return httpx.Response(503 if len(calls) == 1 else 200, text='^SERIES = GSE123')
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=False) as client:
                with patch.object(geo, 'assert_safe_external_url'), patch.object(geo.asyncio, 'sleep', new=AsyncMock()):
                    self.assertIn('GSE123', await geo._get_soft(client, 'GSE123'))
                    with patch.object(geo, 'MAX_BYTES', 3), self.assertRaises(geo.GeoError):
                        await geo._get_soft(client, 'GSE123')
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(302, headers={'Location': 'http://localhost'}))) as client:
                with patch.object(geo, 'assert_safe_external_url'), self.assertRaises(geo.GeoError):
                    await geo._get_soft(client, 'GSE123')
        asyncio.run(run())
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(c.url.host == 'www.ncbi.nlm.nih.gov' and c.url.params['view'] == 'brief' for c in calls))


class GeoRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bioreader-geo-')
        root = Path(self.temp.name)
        self.patchers = [patch.object(main, 'DATA_DIR', root), patch.object(main, 'PAPERS_DIR', root / 'papers'),
                         patch.object(main, 'SETTINGS_FILE', root / 'settings.json')]
        for p in self.patchers:
            p.start()
        self.client = TestClient(main.app)
        self.paper = self.client.post('/api/papers', files={'file': ('synthetic.pdf', fixture_pdf())}).json()
        self.url = '/api/papers/' + self.paper['id'] + '/resources'
        self.directory = main.paper_dir(self.paper['id'])
        self.resource = self.client.post(self.url, json={'title': 'Synthetic cohort', 'identifiers': 'GSE123, GSE456',
            'sample_details': 'Paper used 8 samples', 'purpose': 'Validation'}).json()

    def tearDown(self):
        self.client.close()
        for p in self.patchers:
            p.stop()
        self.temp.cleanup()

    def test_cache_membership_and_no_curated_resource_mutation(self):
        before = (self.directory / 'resources.json').read_bytes()
        self.assertEqual(self.client.get(self.url + '/geo').json(), {'records': {}})
        with patch.object(main, 'fetch_geo_metadata', new=AsyncMock(return_value=metadata())) as fetch:
            first = self.client.post(self.url + '/geo/GSE123')
            self.assertEqual(first.status_code, 200, first.text)
            self.assertFalse(first.json()['cached'])
            self.assertTrue(self.client.post(self.url + '/geo/GSE123').json()['cached'])
            self.assertEqual(fetch.await_count, 1)
            self.assertEqual(self.client.post(self.url + '/geo/GSE999').status_code, 404)
            self.assertEqual(self.client.post(self.url + '/geo/GSM1').status_code, 422)
            self.assertEqual(fetch.await_count, 1)
        self.assertEqual((self.directory / 'resources.json').read_bytes(), before)
        self.assertEqual(self.client.get(self.url + '/geo').json()['records']['GSE123']['geo_sample_count'], 20)

    def test_failed_refresh_retains_cache_and_deleted_resource_is_hidden(self):
        geo.save_geo_cache(self.directory, {'GSE123': metadata()})
        before = (self.directory / 'geo_metadata.json').read_bytes()
        with patch.object(main, 'fetch_geo_metadata', new=AsyncMock(side_effect=geo.GeoError('timeout'))):
            self.assertEqual(self.client.post(self.url + '/geo/GSE123?refresh=true').status_code, 502)
        self.assertEqual((self.directory / 'geo_metadata.json').read_bytes(), before)
        self.client.delete(self.url + '/' + self.resource['id'])
        self.assertEqual(self.client.get(self.url + '/geo').json(), {'records': {}})

    def test_resource_removed_during_fetch_does_not_write(self):
        async def fetch(accession):
            (self.directory / 'resources.json').write_text('[]')
            return metadata()
        with patch.object(main, 'fetch_geo_metadata', side_effect=fetch):
            self.assertEqual(self.client.post(self.url + '/geo/GSE123').status_code, 409)
        self.assertFalse((self.directory / 'geo_metadata.json').exists())

    def test_slower_refresh_cannot_overwrite_a_newer_complete_cached_result(self):
        async def run():
            started, release = asyncio.Event(), asyncio.Event()
            calls = 0
            async def fetch(accession):
                nonlocal calls
                calls += 1
                if calls == 1:
                    started.set()
                    await release.wait()
                    return {**metadata(), 'title': 'Older response'}
                return {**metadata(), 'title': 'Newer response'}
            with patch.object(main, 'fetch_geo_metadata', side_effect=fetch):
                older = asyncio.create_task(main.supplement_geo_metadata(self.paper['id'], 'GSE123', refresh=True))
                await started.wait()
                newer = await main.supplement_geo_metadata(self.paper['id'], 'GSE123', refresh=True)
                self.assertEqual(newer['metadata']['title'], 'Newer response')
                release.set()
                result = await older
            self.assertTrue(result['cached'])
            self.assertEqual(result['metadata']['title'], 'Newer response')
            self.assertEqual(geo.load_geo_cache(self.directory)['GSE123']['title'], 'Newer response')
        asyncio.run(run())

    def test_partial_refresh_keeps_a_more_complete_previous_cache(self):
        original = metadata()
        original.update(samples=[{'accession': 'GSM1', 'source_url': geo.geo_url('GSM1')}], samples_shown=1)
        geo.save_geo_cache(self.directory, {'GSE123': original})
        before = (self.directory / 'geo_metadata.json').read_bytes()
        partial = {**metadata(), 'warnings': ['Sample unavailable']}
        with patch.object(main, 'fetch_geo_metadata', new=AsyncMock(return_value=partial)):
            result = self.client.post(self.url + '/geo/GSE123?refresh=true')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['cached'])
        self.assertEqual(result.json()['metadata']['samples_shown'], 1)
        self.assertTrue(result.json()['metadata']['warnings'])
        self.assertEqual((self.directory / 'geo_metadata.json').read_bytes(), before)

    def test_corrupt_cache_preserved_without_network(self):
        cache = self.directory / 'geo_metadata.json'
        cache.write_text('{broken')
        with patch.object(main, 'fetch_geo_metadata', new=AsyncMock()) as fetch:
            self.assertEqual(self.client.get(self.url + '/geo').status_code, 409)
            self.assertEqual(self.client.post(self.url + '/geo/GSE123').status_code, 409)
            fetch.assert_not_awaited()
        self.assertEqual(cache.read_text(), '{broken')


if __name__ == '__main__':
    unittest.main()
