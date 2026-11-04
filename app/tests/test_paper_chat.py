"""Synthetic PDF / mocked-model chat lifecycle, persistence and source isolation."""
import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_documents import fixture_pdf, main, TestClient
from fastapi import HTTPException
from pypdf import PdfReader, PdfWriter
import chat_sources
import paper_chat


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


async def answer(*args, **kwargs):
    yield '原文显示实验分组 [S1]。'


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bioreader-chat-tests-')
        directory = Path(self.temp.name)
        self.patches = [patch.object(main, 'DATA_DIR', directory), patch.object(main, 'PAPERS_DIR', directory / 'papers'),
                        patch.object(main, 'SETTINGS_FILE', directory / 'settings.json')]
        for item in self.patches:
            item.start()
        main.save_settings({**main.DEFAULT_SETTINGS, 'api_key': 'synthetic-test-key'})
        self.client = TestClient(main.app)
        response = self.client.post('/api/papers', files={'file': ('main.pdf', fixture_pdf(), 'application/pdf')})
        self.assertEqual(response.status_code, 200, response.text)
        self.pid = response.json()['id']
        self.url = '/api/papers/' + self.pid
        self.directory = main.paper_dir(self.pid)
        self.body = {'message': 'What are the sample groups?', 'page': 1}

    def tearDown(self):
        self.client.close()
        paper_chat.active_requests.pop(str(self.directory), None)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def ask(self, body=None, model=answer):
        with patch.object(main, 'stream_chat', model):
            return self.client.post(self.url + '/chat', json=body or self.body)

    def test_success_persisted_sources_real_geometry_and_notes_untouched(self):
        self.client.patch(self.url, json={'notes': 'Manual notes are independent', 'tags': ['manual']})
        before = (self.directory / 'meta.json').read_bytes()
        captured = {}
        async def model(*args, **kwargs):
            captured.update(messages=args[3], options=kwargs)
            yield 'Answer [S1], unsupported [S99].'
        response = self.ask(model=model)
        self.assertEqual(response.status_code, 200, response.text)
        stream = events(response)
        self.assertEqual([item['type'] for item in stream], ['start', 'chunk', 'done'])
        saved = stream[-1]['message']
        self.assertEqual(saved['status'], 'completed')
        self.assertEqual(saved['cited_source_ids'], ['S1'])
        self.assertTrue(captured['options']['require_done'])
        self.assertEqual(captured['options']['max_tokens'], 4096)
        source = saved['sources'][0]
        self.assertTrue(chat_sources.valid_bbox(source['bbox']))
        self.assertIn(source['quote'], captured['messages'][-1]['content'])
        self.assertEqual(source['source_revision'], chat_sources.file_revision(self.directory / 'paper.pdf'))
        detail = self.client.get(f"{self.url}/chat/sources/{saved['id']}/S1")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()['source']['paper_id'], self.pid)
        self.assertEqual(self.client.get(f"{self.url}/chat/sources/{saved['id']}/S99").status_code, 404)
        self.assertEqual(self.client.get(f"{self.url}/chat/sources/{stream[0]['message']['id']}/S1").status_code, 404)
        history = self.client.get(self.url + '/chat').json()
        self.assertEqual(history['total'], 2)
        self.assertEqual(history['messages'][-1], saved)
        self.assertEqual((self.directory / 'meta.json').read_bytes(), before)

    def test_supplement_source_and_removed_document(self):
        supplement = self.client.post(self.url + '/supplements', files={'file': ('supp.pdf', fixture_pdf(marker='SUPPLEMENT'))}).json()['supplements'][0]
        response = self.ask({**self.body, 'document_id': supplement['id'], 'selection': 'SUPPLEMENT synthetic paper for source isolation.'})
        saved = events(response)[-1]['message']
        source = saved['sources'][0]
        self.assertEqual(source['document_id'], supplement['id'])
        self.assertEqual(source['page'], 1)
        self.assertEqual(source['document_name'], 'supp.pdf')
        endpoint = f"{self.url}/chat/sources/{saved['id']}/S1"
        self.assertEqual(self.client.get(endpoint).status_code, 200)
        self.client.delete(self.url + '/supplements/' + supplement['id'])
        self.assertEqual(self.client.get(endpoint).status_code, 404)
        self.assertEqual(self.client.get(self.url + '/chat').json()['messages'][-1]['sources'][0], source)

    def test_replacement_invalidates_saved_sources_without_rebinding(self):
        saved = events(self.ask())[-1]['message']
        endpoint = f"{self.url}/chat/sources/{saved['id']}/S1"
        self.client.post(self.url + '/file', files={'file': ('new.pdf', fixture_pdf(marker='REPLACED'))})
        self.assertEqual(self.client.get(endpoint).status_code, 409)
        self.assertEqual(self.client.get(self.url + '/chat').json()['messages'][-1]['sources'], saved['sources'])

    def test_legacy_lightweight_cache_does_not_render_figures(self):
        cache = self.directory / 'blocks' / 'main.json'
        cache.unlink()
        figures_before = (self.directory / 'figures.json').read_bytes()
        with patch.object(main, 'parse_pdf', side_effect=AssertionError('must not run full import')):
            response = self.ask()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(cache.is_file())
        self.assertTrue(events(response)[-1]['message']['sources'][0]['bbox'])
        self.assertEqual((self.directory / 'figures.json').read_bytes(), figures_before)

    def test_source_change_during_prepare_rejects_without_chat_write(self):
        original = main.prepare_sources
        def changed(*args):
            sources = original(*args)
            (self.directory / 'paper.pdf').write_bytes(fixture_pdf(marker='CHANGED'))
            return sources
        with patch.object(main, 'prepare_sources', changed):
            response = self.ask()
        self.assertEqual(response.status_code, 409)
        self.assertFalse((self.directory / 'chat.json').exists())
        self.assertNotIn(str(self.directory), paper_chat.active_requests)

    def test_corrupt_history_and_cache_are_not_overwritten(self):
        for target, content in [(self.directory / 'chat.json', '[{}]'), (self.directory / 'blocks' / 'main.json', '{broken')]:
            target.write_text(content)
            response = self.ask()
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(target.read_text(), content)
            if target.name == 'chat.json':
                self.assertEqual(self.client.get(self.url + '/chat').status_code, 409)
                target.unlink()
        # Replacing a PDF also leaves a damaged cache recoverable.
        self.client.post(self.url + '/file', files={'file': ('new.pdf', fixture_pdf(marker='NEW'))})
        self.assertEqual((self.directory / 'blocks' / 'main.json').read_text(), '{broken')

    def test_missing_key_invalid_selection_page_and_scanned_pdf_do_not_create_messages(self):
        main.save_settings(main.DEFAULT_SETTINGS)
        self.assertEqual(self.ask().status_code, 400)
        main.save_settings({**main.DEFAULT_SETTINGS, 'api_key': 'synthetic-test-key'})
        self.assertEqual(self.ask({**self.body, 'selection': 'Invented text not present'}).status_code, 422)
        self.assertEqual(self.ask({**self.body, 'page': 2}).status_code, 422)
        self.assertEqual(self.ask({**self.body, 'document_id': '../outside'}).status_code, 404)
        writer = PdfWriter(); writer.add_blank_page(width=612, height=792)
        output = io.BytesIO(); writer.write(output)
        self.client.post(self.url + '/file', files={'file': ('scan.pdf', output.getvalue())})
        self.assertEqual(self.ask().status_code, 422)
        self.assertFalse((self.directory / 'chat.json').exists())

    def test_empty_transport_error_and_timeout_preserve_visible_state(self):
        async def empty(*args, **kwargs):
            if False:
                yield ''
        stream = events(self.ask(model=empty))
        self.assertEqual(stream[-1]['type'], 'error')
        self.assertEqual(stream[-1]['assistant']['status'], 'error')
        async def broken(*args, **kwargs):
            yield 'Partial [S1]'
            raise main.UpstreamError('模型连接提前中断')
        stream = events(self.ask(model=broken))
        self.assertEqual(stream[-1]['assistant']['status'], 'interrupted')
        self.assertEqual(stream[-1]['assistant']['content'], 'Partial [S1]')
        async def slow(*args, **kwargs):
            yield 'Waiting'
            await asyncio.sleep(1)
        with patch.object(main, 'CHAT_TIMEOUT', .01):
            stream = events(self.ask(model=slow))
        self.assertIn('超时', stream[-1]['message'])
        self.assertEqual(self.client.get(self.url + '/chat').json()['messages'][-1]['content'], 'Waiting')

    def test_cancellation_concurrency_recovery_and_live_note_edit(self):
        async def scenario():
            with patch.object(main, 'stream_chat', answer):
                response = await main.ask_paper_chat(self.pid, paper_chat.ChatIn(**self.body))
                self.assertEqual(main.get_paper_chat(self.pid, 100)['messages'][-1]['status'], 'pending')
                with self.assertRaises(HTTPException) as caught:
                    await main.ask_paper_chat(self.pid, paper_chat.ChatIn(**self.body))
                self.assertEqual(caught.exception.status_code, 409)
                iterator = response.body_iterator
                await anext(iterator)  # start
                await anext(iterator)  # first chunk
                main.patch_paper(self.pid, main.PaperPatch(notes='edited during chat'))
                await iterator.aclose()
                self.assertNotIn(str(self.directory), paper_chat.active_requests)
                history = main.get_paper_chat(self.pid, 100)['messages']
                self.assertEqual(history[-1]['status'], 'interrupted')
                self.assertIn('[S1]', history[-1]['content'])
                self.assertEqual(main.read_meta(self.directory)['notes'], 'edited during chat')
                # Simulate a restart after the pending record was already persisted.
                history[-1]['status'] = 'pending'
                paper_chat.save_history(self.directory, history)
                self.assertEqual(main.get_paper_chat(self.pid, 100)['messages'][-1]['status'], 'interrupted')
                # Even an unconsumed response can be finalized safely, twice.
                response = await main.ask_paper_chat(self.pid, paper_chat.ChatIn(**self.body))
                response.finalize_chat(); response.finalize_chat()
                self.assertEqual(main.get_paper_chat(self.pid, 100)['messages'][-1]['status'], 'interrupted')
        asyncio.run(scenario())

    def test_continuous_history_paginates_past_100_and_capacity_never_deletes(self):
        history = []
        for number in range(103):
            user, assistant = paper_chat.new_messages(paper_chat.ChatIn(message=f'Question {number}'), [])
            assistant.update(content='Previous answer [S1]', status='completed')
            history.extend([user, assistant])
        paper_chat.save_history(self.directory, history)
        page = self.client.get(self.url + '/chat').json()
        self.assertEqual((len(page['messages']), page['total'], page['has_more']), (100, 206, True))
        older = self.client.get(self.url + '/chat', params={'before': page['messages'][0]['id']}).json()
        self.assertEqual(len(older['messages']), 100)
        self.assertFalse(set(item['id'] for item in older['messages']) & set(item['id'] for item in page['messages']))
        last = self.client.get(self.url + '/chat', params={'before': older['messages'][0]['id']}).json()
        self.assertEqual((len(last['messages']), last['has_more']), (6, False))
        self.assertEqual(events(self.ask())[-1]['type'], 'done')
        before = (self.directory / 'chat.json').read_bytes()
        with patch.object(paper_chat, 'MAX_HISTORY_BYTES', len(before) + 1000):
            self.assertEqual(self.ask().status_code, 413)
        self.assertEqual((self.directory / 'chat.json').read_bytes(), before)

    def test_rotated_page_text_is_available_without_unreliable_highlight(self):
        reader = PdfReader(io.BytesIO(fixture_pdf()))
        writer = PdfWriter(); writer.add_page(reader.pages[0].rotate(90))
        output = io.BytesIO(); writer.write(output)
        self.client.post(self.url + '/file', files={'file': ('rotated.pdf', output.getvalue())})
        selected = {**self.body, 'selection': 'A controlled test of sample groups, methods, outcomes and limitations.'}
        stream = events(self.ask(selected))
        self.assertEqual(stream[-1]['type'], 'done')
        self.assertTrue(all(source['bbox'] is None for source in stream[-1]['message']['sources']))
        # Existing v1 caches also rebuild via the same text-only path.
        cache_path = self.directory / 'blocks' / 'main.json'
        cache = json.loads(cache_path.read_text()); cache['version'] = 1
        cache['pages']['1'][0]['text'] = 'Old rotated geometry in incorrect reading order'
        cache_path.write_text(json.dumps(cache))
        self.assertEqual(events(self.ask(selected))[-1]['type'], 'done')
        self.assertEqual(json.loads(cache_path.read_text())['version'], 2)

    def test_disk_write_failure_keeps_original_and_releases_active_request(self):
        original_bytes = []
        async def scenario():
            async def failing_model(*args, **kwargs):
                original_bytes.append((self.directory / 'chat.json').read_bytes())
                yield 'Latest unsaved content [S1]'
            with patch.object(main, 'stream_chat', failing_model):
                response = await main.ask_paper_chat(self.pid, paper_chat.ChatIn(**self.body))
                with patch.object(paper_chat, 'atomic_json', side_effect=OSError('disk full')):
                    stream = [json.loads(event.strip()[6:]) async for event in response.body_iterator]
                self.assertEqual(stream[-1]['type'], 'error')
                self.assertIn('可能尚未保存', stream[-1]['message'])
                self.assertEqual(stream[-1]['assistant']['content'], 'Latest unsaved content [S1]')
                self.assertEqual((self.directory / 'chat.json').read_bytes(), original_bytes[0])
                self.assertNotIn(str(self.directory), paper_chat.active_requests)
        asyncio.run(scenario())

    def test_cropped_page_has_no_misplaced_bbox_and_tampered_quote_cannot_jump(self):
        reader = PdfReader(io.BytesIO(fixture_pdf()))
        reader.pages[0].cropbox.lower_left = (40, 40)
        writer = PdfWriter(); writer.add_page(reader.pages[0])
        output = io.BytesIO(); writer.write(output)
        self.client.post(self.url + '/file', files={'file': ('cropped.pdf', output.getvalue())})
        saved = events(self.ask())[-1]['message']
        self.assertTrue(all(source['bbox'] is None for source in saved['sources']))
        history = paper_chat.load_history(self.directory)
        history[-1]['sources'][0]['quote'] = 'A fabricated source quotation'
        paper_chat.save_history(self.directory, history)
        self.assertEqual(self.client.get(f"{self.url}/chat/sources/{saved['id']}/S1").status_code, 409)

    def test_derived_cache_write_failure_does_not_break_material_commit(self):
        revision = main.read_meta(self.directory)['material_revision']
        with patch.object(chat_sources, 'atomic_json', side_effect=OSError('disk full')):
            response = self.client.post(self.url + '/file', files={'file': ('new.pdf', fixture_pdf(marker='NEW'))})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['filename'], 'new.pdf')
            self.assertGreater(response.json()['material_revision'], revision)
            # The old cache is safely stale. Lazy regeneration reports a typed failure.
            failed = self.ask()
            self.assertEqual(failed.status_code, 503, failed.text)
            self.assertIn('缓存', failed.json()['detail'])
        self.assertEqual(events(self.ask())[-1]['type'], 'done')

    def test_provider_http_body_never_enters_saved_chat_errors(self):
        async def rejected(*args, **kwargs):
            raise main.UpstreamError('private echoed credential and manuscript', status_code=429)
            yield ''
        response = self.ask(model=rejected)
        self.assertIn('HTTP 429', events(response)[-1]['message'])
        self.assertNotIn('private echoed', response.text)
        self.assertNotIn('private echoed', (self.directory / 'chat.json').read_text())

    def test_boolean_cache_page_is_rejected_before_it_can_poison_chat(self):
        cache_path = self.directory / 'blocks' / 'main.json'
        cache = json.loads(cache_path.read_text())
        cache['pages']['1'][0]['page'] = True
        malformed = json.dumps(cache)
        cache_path.write_text(malformed)
        self.assertEqual(self.ask().status_code, 409)
        self.assertFalse((self.directory / 'chat.json').exists())
        self.assertEqual(cache_path.read_text(), malformed)


if __name__ == '__main__':
    unittest.main()
