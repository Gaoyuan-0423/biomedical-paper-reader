"""Synthetic PDFs and mocked models: notes cite actual versioned original excerpts."""
import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_documents import fixture_pdf, main, TestClient
from fastapi import HTTPException
import llm
import note_sources as ns
from pypdf import PdfReader, PdfWriter


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


class NoteSourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bioreader-note-source-test-')
        directory = Path(self.temp.name)
        self.patches = [patch.object(main, 'DATA_DIR', directory), patch.object(main, 'PAPERS_DIR', directory / 'papers'),
                        patch.object(main, 'SETTINGS_FILE', directory / 'settings.json')]
        for item in self.patches: item.start()
        main.save_settings({**main.DEFAULT_SETTINGS, 'api_key': 'synthetic-only'})
        self.client = TestClient(main.app)
        paper = self.client.post('/api/papers', files={'file': ('paper.pdf', fixture_pdf())}).json()
        self.pid = paper['id']; self.url = '/api/papers/' + self.pid; self.directory = main.paper_dir(self.pid)

    def tearDown(self):
        ns.active_generations.pop(str(self.directory), None)
        self.client.close()
        for item in reversed(self.patches): item.stop()
        self.temp.cleanup()

    def generate(self, text='关键方法 [N1]，普通文献 [1]，未提供 [N99999]。', model=None):
        async def stream(*args, **kwargs):
            self.sent = args[3]
            yield text
        with patch.object(main, 'stream_chat', model or stream):
            response = self.client.post(self.url + '/read?mode=deep&use_figures=0')
        self.assertEqual(response.status_code, 200, response.text)
        return events(response)

    def resolve(self, source, meta=None):
        meta = meta or self.client.get(self.url).json()
        return self.client.get(f"{self.url}/note-sources/{source['snapshot_id']}/{source['id']}",
                               params={'notes_revision': meta['notes_revision']})

    def test_actual_quotes_valid_ids_and_ordinary_references(self):
        done = self.generate()[-1]
        self.assertEqual(done['type'], 'done')
        self.assertIn('[1]', done['notes']); self.assertIn('[N99999]', done['notes'])
        self.assertNotIn('note-source:' + done['paper']['notes_source_snapshot'] + ':N99999', done['notes'])
        result = self.client.get(self.url + '/note-sources').json()
        self.assertEqual([source['id'] for source in result['sources']], ['N1'])
        source = result['sources'][0]
        self.assertIn(source['quote'], self.sent[1]['content'])
        self.assertTrue(source['bbox']); self.assertEqual(source['page'], 1)
        self.assertEqual(self.resolve(source).status_code, 200)
        self.assertEqual(done['paper']['notes_revision'], 1)
        self.assertEqual(self.resolve({**source, 'id': 'N99999'}).status_code, 404)

    def test_same_page_in_main_and_supplement_have_distinct_origins(self):
        sid = self.client.post(self.url + '/supplements', files={'file': ('supplement.pdf', fixture_pdf(marker='SUPPLEMENT'))}).json()['supplements'][0]['id']
        async def model(*args, **kwargs):
            import re
            supplied = args[3][1]['content']
            supplement_id = re.search(r'\[(N\d+)\] supplement\.pdf', supplied)[1]
            yield f'正文 [N1]。补充 [{supplement_id}]。'
        self.generate(model=model)
        sources = self.client.get(self.url + '/note-sources').json()['sources']
        self.assertEqual([s['document_id'] for s in sources], ['main', sid])
        self.assertEqual([s['page'] for s in sources], [1, 1])
        self.assertTrue(all(self.resolve(source).status_code == 200 for source in sources))
        self.client.delete(self.url + '/supplements/' + sid)
        self.assertEqual(self.resolve(sources[1]).status_code, 404)
        self.assertFalse(self.client.get(self.url + '/note-sources').json()['sources'][1]['source_available'])

    def test_replaced_file_remains_historical_and_cannot_jump(self):
        self.generate()
        source = self.client.get(self.url + '/note-sources').json()['sources'][0]
        self.client.post(self.url + '/file', files={'file': ('replacement.pdf', fixture_pdf(marker='REPLACED'))})
        self.assertEqual(self.resolve(source).status_code, 409)
        current = self.client.get(self.url + '/note-sources').json()['sources'][0]
        self.assertTrue(current['source_changed'])
        self.assertEqual(current['quote'], source['quote'])

    def test_manual_edit_keeps_explicit_link_but_removed_link_and_old_revision_fail(self):
        self.generate()
        before = self.client.get(self.url).json()
        source = self.client.get(self.url + '/note-sources').json()['sources'][0]
        edited = self.client.patch(self.url, json={'notes': '人工补充\n' + before['notes']}).json()
        self.assertGreater(edited['notes_revision'], before['notes_revision'])
        self.assertEqual(self.resolve(source, before).status_code, 409)
        self.assertEqual(self.resolve(source, edited).status_code, 200)
        self.client.patch(self.url, json={'notes': '手写 [N1] 与 [1] 不自动产生引用'})
        self.assertEqual(self.client.get(self.url + '/note-sources').json()['sources'], [])
        self.assertEqual(self.resolve(source).status_code, 404)

    def test_new_generation_never_rebinds_old_ids_and_failed_run_keeps_old_snapshot(self):
        self.generate()
        old = self.client.get(self.url).json()
        source = self.client.get(self.url + '/note-sources').json()['sources'][0]
        async def failed(*args, **kwargs):
            raise main.UpstreamError('Synthetic failure')
            yield ''
        self.assertEqual(self.generate(model=failed)[-1]['type'], 'error')
        self.assertEqual(self.client.get(self.url).json()['notes_source_snapshot'], old['notes_source_snapshot'])
        self.assertEqual(self.resolve(source).status_code, 200)
        self.generate('新版本 [N1]')
        self.assertEqual(self.resolve(source).status_code, 409)
        self.assertTrue((self.directory / 'note-sources' / (old['notes_source_snapshot'] + '.json')).is_file())

    def test_partial_snapshot_save_is_canonical_and_late_partial_cannot_overwrite_edit(self):
        self.generate('旧笔记 [N1]')
        async def failed(*args, **kwargs):
            yield '部分结果 [N1]'
            raise main.UpstreamError('Truncated')
        stream = self.generate(model=failed)
        generation = stream[0]['notes_source_snapshot']
        self.assertEqual(stream[-1]['type'], 'error')
        saved = self.client.patch(self.url, json={'notes': '部分结果 [N1]', 'notes_incomplete': True,
                                                 'notes_source_snapshot': generation})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertIn(f'note-source:{generation}:N1', saved.json()['notes'])
        self.assertEqual(self.resolve(self.client.get(self.url + '/note-sources').json()['sources'][0]).status_code, 200)
        self.assertEqual(self.client.patch(self.url, json={'notes': '部分结果 [N1]', 'notes_source_snapshot': generation}).status_code, 200)
        edited = self.client.patch(self.url, json={'notes': saved.json()['notes'] + '人工编辑'}).json()
        self.assertEqual(self.client.patch(self.url, json={'notes': '更晚到达部分结果 [N1]', 'notes_source_snapshot': generation}).status_code, 409)
        self.assertEqual(self.client.get(self.url).json()['notes'], edited['notes'])

    def test_concurrent_generation_manual_edit_and_cancel_release(self):
        async def model(*args, **kwargs):
            main.patch_paper(self.pid, main.PaperPatch(notes='Manual latest'))
            yield 'Should not replace manual [N1]'
        stream = self.generate(model=model)
        self.assertEqual(stream[-1]['status'], 409)
        self.assertEqual(self.client.get(self.url).json()['notes'], 'Manual latest')
        generation = stream[0]['notes_source_snapshot']
        self.assertEqual(self.client.patch(self.url, json={'notes': 'partial [N1]', 'notes_source_snapshot': generation}).status_code, 409)
        async def scenario():
            response = await main.read_paper(self.pid, 'deep', 0)
            with self.assertRaises(HTTPException) as error:
                await main.read_paper(self.pid, 'deep', 0)
            self.assertEqual(error.exception.status_code, 409)
            await anext(response.body_iterator)
            await response.body_iterator.aclose()
            self.assertNotIn(str(self.directory), ns.active_generations)
        asyncio.run(scenario())

    def test_code_images_links_html_and_legacy_notes_are_not_citations(self):
        samples = ['`[N1]`', '```md\n[N1]\n```', '~~~\n[N1]\n~~~', '![N1](x.png)',
                   '[N1](https://example.org)', '<div>[N1]</div>', '<!-- [N1] -->']
        content = '\n\n'.join(samples) + '\n\n真实来源 [N1]'
        done = self.generate(content)[-1]
        for sample in samples:
            self.assertIn(sample, done['notes'])
        self.assertEqual(done['notes'].count('(note-source:'), 1)
        link = ns.LINK.search(done['notes'])[0]
        self.client.patch(self.url, json={'notes': '`' + link + '`'})
        self.assertEqual(self.client.get(self.url + '/note-sources').json()['sources'], [])
        self.client.patch(self.url, json={'notes': 'Legacy report [1], p.1, [N1]'})
        self.assertEqual(self.client.get(self.url + '/note-sources').json()['sources'], [])

    def test_snapshot_corruption_and_save_failure_preserve_original_notes(self):
        self.generate()
        before = self.client.get(self.url).json()
        path = self.directory / 'note-sources' / (before['notes_source_snapshot'] + '.json')
        path.write_text('{broken')
        self.assertEqual(self.client.get(self.url + '/note-sources').status_code, 409)
        self.assertEqual(path.read_text(), '{broken')
        self.assertEqual(self.client.get(self.url).json()['notes'], before['notes'])
        with patch.object(ns, 'atomic_json', side_effect=OSError('disk full')):
            self.assertEqual(self.generate()[-1]['type'], 'error')
        self.assertEqual(self.client.get(self.url).json()['notes_source_snapshot'], before['notes_source_snapshot'])

    def test_commonmark_literal_regions_and_reference_links_never_become_sources(self):
        snapshot = {'snapshot_id': 'a' * 32, 'sources': [{'id': 'N1'}]}
        samples = [
            '    [N1]\n', '\t[N1]\n', '- Example:\n\n      [N1]\n',
            '> ~~~markdown\n> [N1]\n> ~~~\n',
            '> ```markdown\n> [N1]\n> ```\n',
            '[N1]\n\n[N1]: https://example.org',
            '[N1][]\n\n[N1]: https://example.org',
            '[N1][ref]\n\n[ref]: https://example.org',
            '![image [N1]](https://example.org/a.png)',
            '<div><div>nested</div>[N1]</div>',
            '<div>\n[N1]\n\nStill inside the unclosed HTML container [N1]\n',
            'Text <span>[N1]</span> continues.',
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                bound = ns.bind_citations(sample, snapshot)
                self.assertEqual(bound, sample)
                self.assertEqual(ns.visible_links(bound), [])

    def test_prose_offsets_survive_nested_containers_repeated_labels_and_table_escapes(self):
        snapshot = {'snapshot_id': 'a' * 32, 'sources': [{'id': 'N1'}]}
        sample = ('> **[N1]**\r\n\r\n- Result [N1]\r\n\r\n'
                  '| Literal | Source |\r\n| --- | --- |\r\n'
                  '| `[N1]` | x \\| [N1] |\r\n\r\n'
                  'Text <span>[N1]</span> then [N1].')
        bound = ns.bind_citations(sample, snapshot)
        self.assertEqual(bound.count('(note-source:'), 4)
        self.assertIn('`[N1]`', bound)
        self.assertIn('<span>[N1]</span>', bound)
        self.assertEqual(bound.count('\r\n'), sample.count('\r\n'))
        self.assertEqual(len(ns.visible_links(bound)), 4)
        self.assertEqual(ns.bind_citations(bound, snapshot), bound)
        unicode_lines = 'Claim\u2028[N1]\n\nOther [N1]\n'
        self.assertEqual(len(ns.visible_links(ns.bind_citations(unicode_lines, snapshot))), 2)

    def test_every_export_form_removes_noncanonical_private_targets(self):
        import zipfile
        done = self.generate('Claim [N1]')[-1]
        uri = 'note-source:' + done['paper']['notes_source_snapshot'] + ':N1'
        extra = ('\n\n[custom label](' + uri + ')\n\n![alt](' + uri + ')\n\n'
                 '[reference][target]\n\n[target]: ' + uri + '\n\n'
                 '<a href="' + uri + '">HTML label</a>\n\n'
                 '<a href="' + uri.replace(':', '&#58;', 1) + '">Encoded label</a>\n\n'
                 '<a href="' + uri.replace('note', 'n&#111;te', 1) + '">Encoded scheme</a>\n\n'
                 '<a href=' + uri + '>Unquoted label</a>\n\n'
                 '```md\n' + uri + '\n```\n')
        notes = done['notes'] + extra
        self.client.patch(self.url, json={'notes': notes})
        preview = self.client.post(self.url + '/notes/export-preview', json={'notes': notes})
        markdown = self.client.get(self.url + '/export.md')
        bundle = self.client.post(self.url + '/export.zip', json={'notes': notes})
        self.assertEqual(bundle.status_code, 200, bundle.text if bundle.status_code != 200 else '')
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
            exported = [archive.read(name).decode('utf-8') for name in archive.namelist() if name.endswith('.md')]
        for content in [preview.json()['notes'], markdown.text, *exported]:
            self.assertNotIn('note-source', content.lower())
            self.assertIn('custom label', content)
            self.assertIn('HTML label', content)
            self.assertIn('<a>Encoded scheme</a>', content)
            self.assertIn('<a>Unquoted label</a>', content)
            self.assertIn('笔记原文出处', content)
        self.assertEqual(self.client.get(self.url).json()['notes'], notes)

    def test_private_code_example_does_not_require_or_invent_a_source_snapshot(self):
        snapshot_id = 'a' * 32
        code = '    [N1](note-source:' + snapshot_id + ':N1)\n'
        self.assertEqual(ns.visible_links(code), [])
        result = ns.portable_notes(self.directory, {'notes_source_snapshot': snapshot_id}, code)
        self.assertNotIn('note-source:', result)
        self.assertNotIn('笔记原文出处', result)

    def test_exports_use_portable_labels_and_literal_quotes_not_private_uris(self):
        done = self.generate()[-1]
        exported = self.client.post(self.url + '/notes/export-preview', json={'notes': done['notes']}).json()['notes']
        self.assertNotIn('note-source:', exported)
        self.assertIn('正文 · PDF 第 1 页', exported)
        self.assertIn('笔记原文出处', exported)
        self.assertIn('MAIN synthetic paper', exported)
        self.assertNotIn('note-source:', self.client.get(self.url + '/export.md').text)
        self.assertEqual(self.client.get(self.url).json()['notes'], done['notes'])

    def test_condense_filters_invented_ids_and_final_model_gets_real_quotes(self):
        text, sources, _ = ns.prepare_sources(self.directory, main.read_meta(self.directory))
        async def compress(*args, **kwargs):
            return '压缩要点 [N1]，编造 [N99999]'
        async def run():
            with patch.object(llm, 'chat_once', compress):
                output = [event async for event in llm.condense_long_text('mock', 'mock', 'mock',
                    '[Page 1]\n[N1] ' + 'A' * 13000 + '\n[Page 2]\n[N2] ' + 'B' * 13000,
                    5000, preserve_sources=True)]
            return output[-1]['text']
        summary = asyncio.run(run())
        self.assertNotIn('[N99999]', summary)
        # IDs are filtered per input chunk, so N1 may not leak into the N2 summary.
        self.assertEqual(summary.count('[N1]'), 1)
        body, provided = ns.condensed_context(summary, sources, 5000)
        self.assertLessEqual(len(body), 5000)
        for source in provided:
            self.assertIn(ns.source_text(source), body)
        self.assertNotIn('[N99999]', body)

    def test_long_document_generation_cites_only_originals_reattached_to_final_prompt(self):
        reader = PdfReader(io.BytesIO(fixture_pdf()))
        writer = PdfWriter()
        for _ in range(40):
            writer.add_page(reader.pages[0])
        output = io.BytesIO(); writer.write(output)
        self.client.post(self.url + '/file', files={'file': ('long.pdf', output.getvalue())})
        self.client.put('/api/settings', json={'max_chars': 5000})
        condensed_calls = []
        async def compress(*args, **kwargs):
            condensed_calls.append(args[3])
            return '要点 [N1]，假的来源 [N99999]'
        with patch.object(llm, 'chat_once', compress):
            done = self.generate('关键结论 [N1]，不允许 [N99999]')[-1]
        self.assertEqual(done['type'], 'done')
        self.assertTrue(condensed_calls)
        self.assertNotIn('[N99999]', self.sent[1]['content'])
        snapshot = ns.load_snapshot(self.directory, done['paper']['notes_source_snapshot'])
        self.assertLess(len(snapshot['sources']), 40)
        for source in snapshot['sources']:
            self.assertIn(ns.source_text(source), self.sent[1]['content'])
        self.assertEqual(self.resolve(self.client.get(self.url + '/note-sources').json()['sources'][0]).status_code, 200)

    def test_changed_then_restored_manual_notes_still_reject_older_generation(self):
        self.client.patch(self.url, json={'notes': 'Original'})
        async def model(*args, **kwargs):
            main.patch_paper(self.pid, main.PaperPatch(notes='Temporary manual edit'))
            main.patch_paper(self.pid, main.PaperPatch(notes='Original'))
            yield 'New model output [N1]'
        result = self.generate(model=model)
        self.assertEqual(result[-1]['status'], 409)
        self.assertEqual(self.client.get(self.url).json()['notes'], 'Original')


if __name__ == '__main__':
    unittest.main()
