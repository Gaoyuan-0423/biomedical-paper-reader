"""Automatic labels are additive, bounded, and cannot overwrite newer edits."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_documents import fixture_pdf, main, TestClient
from paper_tags import TEXT_LIMIT, NOTES_LIMIT


class AutoTagTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bioreader-tags-')
        root = Path(self.temp.name)
        self.patchers = [patch.object(main, 'DATA_DIR', root),
                         patch.object(main, 'PAPERS_DIR', root / 'papers'),
                         patch.object(main, 'SETTINGS_FILE', root / 'settings.json'),
                         patch.object(main, 'TAG_RETRY_DELAY_SECONDS', 0)]
        for patcher in self.patchers:
            patcher.start()
        self.client = TestClient(main.app)
        response = self.client.post('/api/papers', files={'file': ('main.pdf', fixture_pdf())})
        self.assertEqual(response.status_code, 200, response.text)
        self.paper = response.json()
        self.url = '/api/papers/' + self.paper['id']
        self.directory = main.paper_dir(self.paper['id'])
        self.client.patch(self.url, json={'tags': ['人工标签', 'scRNA-seq'],
                                          'notes': '# Saved note\nA supported macrophage finding.',
                                          'reading_status': 'reading'})
        self.client.put('/api/settings', json={'api_key': 'test-only-key', 'model': 'mock-model'})

    def tearDown(self):
        self.client.close()
        for patcher in self.patchers:
            patcher.stop()
        self.temp.cleanup()

    def call(self, output):
        model = AsyncMock(return_value=output)
        with patch.object(main, 'chat_once', model):
            response = self.client.post(self.url + '/tags/auto')
        return response, model

    def test_additive_tags_keep_human_labels_and_notes_unchanged(self):
        before = json.loads((self.directory / 'meta.json').read_text(encoding='utf-8'))
        result, model = self.call('```json\n[" SCRNA-seq ", "肿瘤免疫", "巨噬细胞", "巨噬细胞"]\n```')
        self.assertEqual(result.status_code, 200, result.text)
        result = result.json()
        self.assertEqual(result['added_tags'], ['肿瘤免疫', '巨噬细胞'])
        self.assertEqual(result['paper']['tags'], ['人工标签', 'scRNA-seq', '肿瘤免疫', '巨噬细胞'])
        self.assertNotIn('notes', result['paper'])
        self.assertEqual(model.await_count, 1)
        after = json.loads((self.directory / 'meta.json').read_text(encoding='utf-8'))
        self.assertEqual(after['tags_revision'], before['tags_revision'] + 1)
        for key in before:
            if key not in ('tags', 'tags_revision'):
                self.assertEqual(before[key], after[key], key)
        repeated, _ = self.call('["肿瘤免疫"]')
        self.assertEqual(repeated.json()['added_tags'], [])
        self.assertEqual(repeated.json()['paper']['tags_revision'], after['tags_revision'])

    def test_empty_evidence_and_one_or_two_supported_tags_are_handled(self):
        before = (self.directory / 'meta.json').read_bytes()
        empty, _ = self.call('[]')
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()['added_tags'], [])
        self.assertIn('未识别到', empty.json()['message'])
        self.assertEqual((self.directory / 'meta.json').read_bytes(), before)
        short, _ = self.call('["免疫微环境", "巨噬细胞"]')
        self.assertEqual(short.json()['added_tags'], ['免疫微环境', '巨噬细胞'])

    def test_invalid_model_output_never_mutates_the_paper(self):
        before = (self.directory / 'meta.json').read_bytes()
        invalid = ['not JSON', '{"tags":["A"],"other":true}', '[1, "A"]', '[""]', '["<script>alert(1)</script>"]',
                   '["https://example.org"]', json.dumps(['x' * 41]), json.dumps(['A', 'B', 'C', 'D', 'E', 'F', 'G']),
                   '```python\n["A"]\n```', '["line\\nbreak"]']
        for output in invalid:
            with self.subTest(output=output):
                result, _ = self.call(output)
                self.assertEqual(result.status_code, 502, result.text)
                self.assertEqual((self.directory / 'meta.json').read_bytes(), before)

    def test_missing_key_and_upstream_failure_preserve_everything(self):
        before = (self.directory / 'meta.json').read_bytes()
        model = AsyncMock(return_value='["A"]')
        with patch.object(main, 'load_settings', return_value={'api_key': ''}), patch.object(main, 'chat_once', model):
            result = self.client.post(self.url + '/tags/auto')
        self.assertEqual(result.status_code, 400)
        model.assert_not_awaited()
        for error in (main.UpstreamError('模型服务限流，请稍后重试'), RuntimeError('SENSITIVE RAW ERROR')):
            with patch.object(main, 'chat_once', AsyncMock(side_effect=error)):
                result = self.client.post(self.url + '/tags/auto')
            self.assertEqual(result.status_code, 502)
            self.assertNotIn('SENSITIVE', result.text)
            self.assertEqual((self.directory / 'meta.json').read_bytes(), before)

    def test_timeout_cancels_model_and_does_not_mutate_paper(self):
        before = (self.directory / 'meta.json').read_bytes()
        cancelled = []

        async def never_finishes(*args, **kwargs):
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.append(True)

        with patch.object(main, 'TAG_TIMEOUT_SECONDS', 0.01), patch.object(main, 'chat_once', never_finishes):
            result = self.client.post(self.url + '/tags/auto')
        self.assertEqual(result.status_code, 504)
        self.assertEqual(len(cancelled), 2)
        self.assertEqual((self.directory / 'meta.json').read_bytes(), before)

    def test_human_edit_then_restore_tags_rejects_result_without_locking_await(self):
        before = self.client.get(self.url).json()

        def edit_twice():
            main.patch_paper(self.paper['id'], main.PaperPatch(tags=['人工变更']))
            main.patch_paper(self.paper['id'], main.PaperPatch(tags=before['tags']))

        async def model(*args, **kwargs):
            await asyncio.to_thread(edit_twice)
            return '["过期标签"]'

        with patch.object(main, 'chat_once', model):
            result = self.client.post(self.url + '/tags/auto')
        self.assertEqual(result.status_code, 409, result.text)
        after = self.client.get(self.url).json()
        self.assertEqual(after['tags'], before['tags'])
        self.assertEqual(after['tags_revision'], before['tags_revision'] + 2)
        self.assertEqual(after['notes'], before['notes'])

    def test_source_title_abstract_or_same_second_note_changes_reject_stale_tags(self):
        for field in ('material_revision', 'title', 'abstract', 'notes'):
            with self.subTest(field=field):
                before = self.client.get(self.url).json()

                def mutate():
                    with main.storage_lock:
                        current = main.read_meta(self.directory)
                        current[field] = current.get(field, 0) + 1 if field == 'material_revision' else 'new value ' + field
                        # Keep notes_updated_at unchanged to exercise the text digest.
                        main.write_meta(self.directory, current)

                async def model(*args, **kwargs):
                    await asyncio.to_thread(mutate)
                    return '["过期标签"]'

                with patch.object(main, 'chat_once', model):
                    result = self.client.post(self.url + '/tags/auto')
                self.assertEqual(result.status_code, 409, result.text)
                after = self.client.get(self.url).json()
                self.assertEqual(after['tags'], before['tags'])
                self.assertEqual(after['tags_revision'], before['tags_revision'])

    def test_context_uses_only_bounded_current_paper_and_saved_notes(self):
        (self.directory / 'text.txt').write_text('P' * (TEXT_LIMIT + 100) + 'SECRET_TAIL', encoding='utf-8')
        self.client.patch(self.url, json={'notes': 'N' * (NOTES_LIMIT + 100) + 'SECRET_NOTE_TAIL'})
        other_id, other = main.new_paper_dir()
        main.write_meta(other, {**main.blank_meta(other_id), 'notes': 'UNRELATED_PRIVATE_PAPER'})
        result, model = self.call('[]')
        self.assertEqual(result.status_code, 200)
        messages = model.await_args.args[3]
        material = json.loads(messages[1]['content'])
        self.assertEqual(len(material['paper_excerpt']), TEXT_LIMIT)
        self.assertEqual(len(material['saved_notes']), NOTES_LIMIT)
        self.assertNotIn('SECRET_TAIL', messages[1]['content'])
        self.assertNotIn('SECRET_NOTE_TAIL', messages[1]['content'])
        self.assertNotIn('UNRELATED_PRIVATE_PAPER', messages[1]['content'])
        self.assertEqual(model.await_args.kwargs['timeout'], 60.0)
        self.assertEqual(model.await_args.kwargs['max_tokens'], 2048)

    def test_transient_failure_retries_once_and_preserves_notes(self):
        before = self.client.get(self.url).json()['notes']
        for error in (asyncio.TimeoutError(), main.httpx.ConnectError('temporary network failure'),
                      main.httpx.RemoteProtocolError('server disconnected early'),
                      main.UpstreamError('触发限流', status_code=429),
                      main.UpstreamError('上游故障', status_code=503)):
            with self.subTest(error=type(error).__name__):
                model = AsyncMock(side_effect=[error, '["免疫微环境"]'])
                with patch.object(main, 'chat_once', model):
                    response = self.client.post(self.url + '/tags/auto')
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(model.await_count, 2)
                self.assertEqual(self.client.get(self.url).json()['notes'], before)

    def test_format_retry_and_unambiguous_wrapped_json(self):
        model = AsyncMock(side_effect=['标签如下：免疫微环境', '```json\n{"tags": ["免疫微环境"]}\n```'])
        with patch.object(main, 'chat_once', model):
            response = self.client.post(self.url + '/tags/auto')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['added_tags'], ['免疫微环境'])
        self.assertEqual(model.await_count, 2)
        self.assertIn('合法 JSON', model.await_args.args[3][-1]['content'])

    def test_permanent_auth_error_is_not_retried(self):
        model = AsyncMock(side_effect=main.UpstreamError('鉴权失败', status_code=401))
        before = (self.directory / 'meta.json').read_bytes()
        with patch.object(main, 'chat_once', model):
            response = self.client.post(self.url + '/tags/auto')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(model.await_count, 1)
        self.assertEqual((self.directory / 'meta.json').read_bytes(), before)

    def test_transport_preserves_http_status_for_retry_decisions(self):
        import llm
        import httpx
        client_type = httpx.AsyncClient
        for status in (401, 429, 503):
            with self.subTest(status=status):
                transport = httpx.MockTransport(lambda request: httpx.Response(
                    status, json={'error': {'message': 'synthetic upstream response'}}))
                def client(**kwargs):
                    return client_type(transport=transport, **kwargs)
                with patch.object(llm, 'assert_safe_external_url'), patch.object(llm.httpx, 'AsyncClient', client):
                    with self.assertRaises(main.UpstreamError) as raised:
                        asyncio.run(llm.chat_once('https://example.org/v1', 'test-key', 'test-model',
                                                  [{'role': 'user', 'content': 'Synthetic material'}]))
                self.assertEqual(raised.exception.status_code, status)

    def test_changed_notes_prevent_wasted_retry_after_transient_failure(self):
        async def changed(*args, **kwargs):
            main.patch_paper(self.paper['id'], main.PaperPatch(notes='Updated while identifying'))
            raise main.UpstreamError('暂时限流', status_code=429)
        model = AsyncMock(side_effect=changed)
        with patch.object(main, 'chat_once', model):
            response = self.client.post(self.url + '/tags/auto')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(model.await_count, 1)
        self.assertEqual(self.client.get(self.url).json()['notes'], 'Updated while identifying')

    def test_tag_limit_and_legacy_revision_do_not_drop_existing_labels(self):
        tags = ['人工标签' + str(i) for i in range(29)]
        self.client.patch(self.url, json={'tags': tags})
        path = self.directory / 'meta.json'
        raw = json.loads(path.read_text(encoding='utf-8'))
        raw.pop('tags_revision')
        path.write_text(json.dumps(raw), encoding='utf-8')
        self.assertEqual(self.client.get(self.url).json()['tags_revision'], 0)
        result, _ = self.call('["新标签一", "新标签二"]')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['paper']['tags'], tags + ['新标签一'])
        full, _ = self.call('["新标签二"]')
        self.assertEqual(full.json()['added_tags'], [])
        self.assertIn('30', full.json()['message'])


if __name__ == '__main__':
    unittest.main()
