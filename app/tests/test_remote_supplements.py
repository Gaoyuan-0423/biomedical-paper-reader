"""A mocked official PMC record, through the reader's normal document endpoints."""
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

from test_documents import TestClient, fixture_pdf, main
import pmc_materials as pmc


def md5(data):
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


class RemoteSupplementTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(patch.object(main, 'PAPERS_DIR', Path(temp.name) / 'papers'))
        self.client = self.enterContext(TestClient(main.app))
        primary = fixture_pdf()
        paper = self.client.post('/api/papers', files={'file': ('main.pdf', primary)}).json()
        self.directory = main.paper_dir(paper['id'])
        paper['doi'] = '10.1234/fixture'
        main.write_meta(self.directory, paper)
        self.root = '/api/papers/' + paper['id']
        self.pdf = fixture_pdf('Figure S1', 'Supplement')
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as archive:
            archive.writestr('[Content_Types].xml', '')
            archive.writestr('xl/workbook.xml', '')
        self.xlsx = out.getvalue()
        xml = b'''<article xmlns:xlink="http://www.w3.org/1999/xlink">
            <graphic xlink:href="unrelated.pdf"/>
            <supplementary-material><label>Supplementary table</label>
            <media xlink:href="table.xlsx"/></supplementary-material>
            <supplementary-material xlink:href="fig.pdf"/>
            <inline-supplementary-material xlink:href="../absent.pdf"/>
            </article>'''
        self.files = {'article.xml': xml, 'table.xlsx': self.xlsx, 'fig.pdf': self.pdf, 'unrelated.pdf': primary}
        def uri(name):
            return 's3://pmc-oa-opendata/PMC123.1/' + name + '?md5=' + md5(self.files[name])
        self.manifest = {'pmcid': 'PMC123', 'version': 1, 'is_pmc_openaccess': True,
                         'doi': paper['doi'], 'xml_url': uri('article.xml'),
                         'media_urls': [uri(n) for n in self.files if n != 'article.xml']}
        self.reads = []

        async def fetch(url, host, limit):
            self.reads.append(url)
            parts = urlsplit(url)
            self.assertEqual(parts.hostname, host)
            if host == pmc.CONVERTER_HOST:
                return json.dumps({'status': 'ok', 'records': [{'doi': paper['doi'], 'pmcid': 'PMC123'}]}).encode()
            if parts.path == '/':
                return b'<ListBucketResult><Contents><Key>metadata/PMC123.1.json</Key></Contents></ListBucketResult>'
            if parts.path == '/metadata/PMC123.1.json':
                return json.dumps(self.manifest).encode()
            return self.files[parts.path.rsplit('/', 1)[-1]]
        self.enterContext(patch.object(pmc, '_fetch', side_effect=fetch))

    def discover(self):
        r = self.client.get(self.root + '/supplement-candidates')
        self.assertEqual(r.status_code, 200, r.text)
        return {row['filename']: row for row in r.json()['candidates']}

    def acquire(self, row):
        return self.client.post(self.root + '/supplement-candidates/' + row['id'])

    def test_only_selected_files_are_saved_and_document_sources_stay_separate(self):
        rows = self.discover()
        self.assertEqual(set(rows), {'fig.pdf', 'table.xlsx'})
        self.assertFalse(any(u.endswith(('.xlsx', '.pdf')) for u in self.reads))
        table = self.acquire(rows['table.xlsx'])
        self.assertEqual(table.status_code, 200, table.text)
        rec = table.json()['supplement_files'][0]
        self.assertEqual(rec['filename'], 'table.xlsx')
        self.assertEqual(rec['article_version'], 'PMC123.1')
        self.assertEqual(rec['source_label'], 'Supplementary table')
        self.assertEqual(rec['mime'], pmc.MIMES['.xlsx'])
        self.assertEqual(len(table.json()['supplements']), 0)
        self.assertFalse(any(u.endswith('.pdf') for u in self.reads))
        r = self.client.get(self.root + '/supplement-files/' + rec['id'])
        self.assertEqual(r.content, self.xlsx)
        self.assertIn('attachment', r.headers['content-disposition'])
        self.assertEqual(r.headers['content-type'], pmc.MIMES['.xlsx'])
        self.assertEqual(r.headers['x-content-type-options'], 'nosniff')
        pdf = self.acquire(rows['fig.pdf'])
        self.assertEqual(pdf.status_code, 200, pdf.text)
        doc = pdf.json()['supplements'][0]
        self.assertEqual(doc['source_url'], rows['fig.pdf']['source_url'])
        self.assertEqual(main.document_path(self.directory, doc['id']).read_bytes(), self.pdf)
        self.assertEqual(main.document_path(self.directory).read_bytes(), self.files['unrelated.pdf'])
        count = len(self.reads)
        self.assertEqual(len(self.acquire(rows['fig.pdf']).json()['supplements']), 1)
        self.assertEqual(len(self.acquire(rows['table.xlsx']).json()['supplement_files']), 1)
        self.assertEqual(len(self.reads), count)

    def test_changed_record_content_and_unknown_choice_do_not_attach(self):
        rows = self.discover()
        self.files['table.xlsx'] = b'changed at source'
        self.assertEqual(self.acquire(rows['table.xlsx']).status_code, 502)
        self.assertEqual(self.acquire({'id': 'invalid'}).status_code, 409)
        meta = main.read_meta(self.directory)
        meta['doi'] = '10.1234/other'
        main.write_meta(self.directory, meta)
        self.assertEqual(self.acquire(rows['fig.pdf']).status_code, 409)
        self.assertFalse(main.read_meta(self.directory).get('supplement_files'))

    def test_unavailable_and_mismatched_manifests_are_excluded(self):
        self.manifest['is_pmc_openaccess'] = False
        self.assertEqual(self.discover(), {})
        self.manifest.update(is_pmc_openaccess=True, doi='10.1234/other')
        self.assertEqual(self.discover(), {})

    def test_object_scope_and_download_type_validation(self):
        for value in ('s3://untrusted/PMC123.1/f.pdf', 's3://pmc-oa-opendata/PMC999.1/f.pdf',
                      's3://pmc-oa-opendata/PMC123.1/..%2Foutside.pdf'):
            self.assertIsNone(pmc.object_location(value + '?md5=' + '0'*32, 'PMC123.1'))
        for name, data in [('x.csv', b'<html>login</html>'), ('x.xlsx', b'invalid'), ('x.pdf', self.xlsx)]:
            with self.assertRaises(pmc.PMCError):
                pmc.validate_file(name, data)


if __name__ == '__main__':
    unittest.main()
