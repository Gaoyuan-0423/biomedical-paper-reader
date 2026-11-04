"""Search actual synthetic PDF text and saved notes without a model or network."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_documents import fixture_pdf
from pypdf import PdfWriter
from fastapi import HTTPException
import chat_sources
from library_search import search_library


class LibrarySearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bioreader-library-search-')
        self.root = Path(self.temp.name)
        self.meta = {'id': 'a' * 32, 'title': 'Synthetic title', 'has_file': True, 'pages': 1,
                     'notes': 'Saved note: controlled test of sample groups.', 'supplements': []}
        self.directory = self.root / self.meta['id']
        self.directory.mkdir()
        (self.directory / 'paper.pdf').write_bytes(fixture_pdf())
        (self.directory / 'meta.json').write_text(json.dumps(self.meta))

    def tearDown(self):
        self.temp.cleanup()

    def search(self, query='sample groups', **kwargs):
        return search_library([(self.directory, self.meta)], query, **kwargs)

    def test_pdf_and_notes_have_distinct_real_navigation_sources(self):
        before = (self.directory / 'meta.json').read_bytes()
        result = self.search()
        self.assertEqual(result['warning_count'], 0)
        note = next(item for item in result['items'] if item['kind'] == 'notes')
        pdf = next(item for item in result['items'] if item['kind'] == 'pdf')
        self.assertIsNone(note['page']); self.assertIsNone(note['source'])
        self.assertEqual(pdf['page'], 1)
        verified = chat_sources.verified_source(self.directory, self.meta, pdf['source'])
        self.assertIn('sample groups', verified['quote'])
        self.assertEqual(pdf['source_revision'], chat_sources.file_revision(self.directory / 'paper.pdf'))
        self.assertEqual((self.directory / 'meta.json').read_bytes(), before)

    def test_scope_and_saved_notes_only_do_not_read_pdf_or_search_abstract(self):
        self.meta['abstract'] = 'absent phrase is not a PDF'
        with patch('library_search.load_document_pages', side_effect=AssertionError('PDF must not be read')):
            result = self.search(scope='notes')
        self.assertTrue(all(item['kind'] == 'notes' for item in result['items']))
        self.assertTrue(all(item['kind'] == 'pdf' for item in self.search(scope='pdf')['items']))
        self.assertEqual(self.search('absent phrase')['items'], [])

    def test_supplement_is_separate_from_main_and_can_be_verified(self):
        sid = 'b' * 32
        (self.directory / 'supplements').mkdir()
        (self.directory / 'supplements' / (sid + '.pdf')).write_bytes(fixture_pdf(marker='SUPPLEMENT'))
        self.meta['supplements'] = [{'id': sid, 'filename': 'supplement.pdf', 'pages': 1}]
        result = self.search('SUPPLEMENT', scope='pdf')
        self.assertTrue(result['items'])
        self.assertTrue(all(item['document_id'] == sid for item in result['items']))
        self.assertEqual(result['items'][0]['source_name'], 'supplement.pdf')
        chat_sources.verified_source(self.directory, self.meta, result['items'][0]['source'])

    def test_unicode_casefold_whitespace_and_snippet_remain_original_text(self):
        self.meta['notes'] = 'prefix ' * 40 + '胃癌样品 ＡＮＸＡ５\n tumor\tSTUDY Straße' + ' suffix' * 50
        for query in ('anxa5 tumor study', '胃癌 样品', 'STRASSE'):
            result = self.search(query, scope='notes')
            self.assertEqual(len(result['items']), 1)
            self.assertLess(len(result['items'][0]['snippet']), 500)
        snippet = self.search('ANXA5 tumor', scope='notes')['items'][0]['snippet']
        self.assertIn('ＡＮＸＡ５\n tumor', snippet)
        self.assertNotIn('<mark>', snippet)

    def test_pagination_no_hidden_20_result_cap(self):
        entries = []
        for index in range(45):
            entries.append((self.directory, {**self.meta, 'id': f'{index:032x}', 'has_file': False,
                                             'notes': 'matching phrase'}))
        first = search_library(entries, 'matching phrase')
        second = search_library(entries, 'matching phrase', offset=first['next_offset'])
        third = search_library(entries, 'matching phrase', offset=second['next_offset'])
        self.assertEqual([len(r['items']) for r in (first, second, third)], [20, 20, 5])
        self.assertEqual([r['has_more'] for r in (first, second, third)], [True, True, False])
        self.assertEqual(len({i['paper_id'] for r in (first, second, third) for i in r['items']}), 45)

    def test_existing_cache_reused_once_per_document(self):
        self.search()
        with patch('chat_sources.pdfplumber.open', side_effect=AssertionError('existing cache')), \
                patch('library_search.load_document_pages', wraps=chat_sources.load_document_pages) as load:
            self.assertTrue(self.search()['items'])
            self.assertEqual(load.call_count, 1)

    def test_replaced_pdf_rebuilds_cache_and_old_source_fails(self):
        old = self.search('MAIN synthetic', scope='pdf')['items'][0]
        (self.directory / 'paper.pdf').write_bytes(fixture_pdf(marker='REPLACEMENT'))
        self.assertEqual(self.search('MAIN synthetic', scope='pdf')['items'], [])
        self.assertTrue(self.search('REPLACEMENT synthetic', scope='pdf')['items'])
        with self.assertRaises(HTTPException) as error:
            chat_sources.verified_source(self.directory, self.meta, old['source'])
        self.assertEqual(error.exception.status_code, 409)

    def test_damaged_cache_preserved_and_warning_does_not_hide_notes(self):
        cache = self.directory / 'blocks' / 'main.json'
        cache.parent.mkdir(); cache.write_text('{broken')
        result = self.search()
        self.assertEqual(result['warning_count'], 1)
        self.assertEqual([item['kind'] for item in result['items']], ['notes'])
        self.assertEqual(cache.read_text(), '{broken')

    def test_missing_files_scanned_pdf_and_no_pdf_metadata(self):
        (self.directory / 'paper.pdf').unlink()
        self.assertEqual(self.search()['warning_count'], 1)
        writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
        stream = io.BytesIO(); writer.write(stream)
        (self.directory / 'paper.pdf').write_bytes(stream.getvalue())
        result = self.search(scope='pdf')
        self.assertEqual(result['items'], [])
        self.assertIn('文字识别', result['warnings'][0]['message'])
        self.meta['has_file'] = False
        self.assertEqual(self.search()['warning_count'], 0)

    def test_change_while_searching_is_reported_without_stale_pdf_result(self):
        with patch('library_search.file_revision', return_value='changed'):
            result = self.search(scope='pdf')
        self.assertEqual(result['items'], [])
        self.assertIn('变化', result['warnings'][0]['message'])

    def test_warning_list_bounded_but_total_not_hidden(self):
        entries = [(self.directory / str(i), {**self.meta, 'id': f'{i:032x}'}) for i in range(24)]
        result = search_library(entries, 'sample', scope='pdf')
        self.assertEqual(len(result['warnings']), 20)
        self.assertEqual(result['warning_count'], 24)

    def test_phrase_crossing_same_page_blocks_has_verified_starting_block(self):
        revision = chat_sources.file_revision(self.directory / 'paper.pdf')
        chat_sources.save_parsed_blocks(self.directory, 'main', revision, [{'number': 1, 'blocks': [
            {'text': 'A controlled test of sample', 'bbox': None},
            {'text': 'groups, methods, outcomes and limitations.', 'bbox': None}]}])
        result = self.search('sample groups', scope='pdf')
        self.assertEqual(len(result['items']), 1)
        item = result['items'][0]
        self.assertIn('sample\ngroups', item['snippet'])
        self.assertEqual(item['source']['block_id'], 'p1-b1')
        chat_sources.verified_source(self.directory, self.meta, item['source'])

    def test_input_validation(self):
        for query in ('', 'a', 'a ' * 200, '  a  '):
            with self.assertRaises(HTTPException): self.search(query)
        for kwargs in ({'scope': 'remote'}, {'offset': -1}, {'limit': 21}, {'offset': True}, {'limit': 0}):
            with self.assertRaises(HTTPException): self.search(**kwargs)


if __name__ == '__main__':
    unittest.main()
