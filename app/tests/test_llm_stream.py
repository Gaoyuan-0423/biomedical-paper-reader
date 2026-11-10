"""Streaming and non-streaming replies must not disguise incomplete answers."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import llm


def delta(text='', finish=None):
    return 'data: ' + json.dumps({'choices': [{
        'delta': {'content': text}, 'finish_reason': finish,
    }]}) + '\n\n'


class StreamCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, body, strict=True):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body))
        client = httpx.AsyncClient(transport=transport)
        parts = []
        with patch.object(llm, 'assert_safe_external_url'), \
                patch.object(llm.httpx, 'AsyncClient', return_value=client):
            async for text in llm.stream_chat('https://model.example/v1', 'test-key', 'mock',
                                              [{'role': 'user', 'content': 'test'}],
                                              require_done=strict):
                parts.append(text)
        return ''.join(parts)

    async def test_done_marker_and_stop_are_supported(self):
        self.assertEqual(await self.collect(delta('answer') + 'data: [DONE]\n\n'), 'answer')
        self.assertEqual(await self.collect(delta('answer', 'stop')), 'answer')

    async def test_unexpected_eof_is_not_success(self):
        for body in ('', delta('partial')):
            with self.subTest(body=body), self.assertRaises(llm.UpstreamError):
                await self.collect(body)

    async def test_output_limit_and_filter_are_not_success(self):
        for finish in ('length', 'content_filter', 'tool_calls'):
            with self.subTest(finish=finish), self.assertRaises(llm.UpstreamError):
                await self.collect(delta('partial', finish) + 'data: [DONE]\n\n')

    async def test_error_event_and_invalid_data_are_not_success(self):
        for event in ('{"error":{"message":"private upstream detail"}}', 'bad json', 'null'):
            with self.subTest(event=event), self.assertRaises(llm.UpstreamError) as error:
                await self.collect(delta('partial') + 'data: ' + event + '\n\n')
            self.assertNotIn('private upstream detail', str(error.exception))

    async def test_existing_note_stream_keeps_legacy_eof_behavior(self):
        self.assertEqual(await self.collect(delta('legacy'), strict=False), 'legacy')


class NonStreamingCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def complete(self, body, *, raw=False, reasoning_effort=None,
                       base_url='https://model.example/v1', model='mock'):
        body = body if raw else json.dumps(body)

        def respond(request):
            payload = json.loads(request.content)
            self.assertIs(payload['stream'], False)
            self.assertEqual(payload['max_tokens'], 1200)
            if reasoning_effort is None:
                self.assertNotIn('reasoning_effort', payload)
            else:
                self.assertEqual(payload['reasoning_effort'], reasoning_effort)
            return httpx.Response(200, text=body)

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch.object(llm, 'assert_safe_external_url'), \
                patch.object(llm.httpx, 'AsyncClient', return_value=client):
            return await llm.chat_once(base_url, 'test-key', model,
                                       [{'role': 'user', 'content': 'test'}], max_tokens=1200,
                                       reasoning_effort=reasoning_effort)

    def response(self, content, finish='stop', **message_fields):
        return {'choices': [{'message': {'content': content, **message_fields}, 'finish_reason': finish}]}

    async def test_complete_and_legacy_answers_preserve_text(self):
        for finish in ('stop', None):
            with self.subTest(finish=finish):
                self.assertEqual(await self.complete(self.response(' final answer\n', finish)), ' final answer\n')
        self.assertEqual(await self.complete({'choices': [{'message': {'content': 'legacy'}}]}), 'legacy')

    async def test_validated_text_blocks_are_supported(self):
        body = self.response([{'type': 'text', 'text': 'first '}, {'type': 'text', 'text': 'second'}])
        self.assertEqual(await self.complete(body), 'first second')

    async def test_explicit_reasoning_effort_is_forwarded_in_request_payload(self):
        self.assertEqual(await self.complete(self.response('answer'), reasoning_effort='low'), 'answer')

    async def test_direct_completion_does_not_change_provider_reasoning_defaults(self):
        self.assertEqual(await self.complete(self.response('answer'),
            base_url='https://open.bigmodel.cn/api/paas/v4', model='glm-5.3-flash'), 'answer')

    async def test_reasoning_exhaustion_is_classified_without_leaking_reasoning(self):
        body = self.response(None, 'length', reasoning_content='private reasoning trace')
        body['usage'] = {'completion_tokens': 1200, 'completion_tokens_details': {'reasoning_tokens': 1194}}
        with self.assertRaises(llm.CompletionError) as error:
            await self.complete(body)
        self.assertIsInstance(error.exception, llm.UpstreamError)
        self.assertEqual(error.exception.reason, 'length')
        self.assertEqual(error.exception.finish_reason, 'length')
        self.assertFalse(error.exception.has_content)
        self.assertNotIn('private reasoning trace', str(error.exception))

    async def test_length_is_never_success_even_with_partial_answer(self):
        for content in ('partial answer', '', ' \n\t'):
            with self.subTest(content=content), self.assertRaises(llm.CompletionError) as error:
                await self.complete(self.response(content, 'length'))
            self.assertEqual(error.exception.reason, 'length')
            self.assertEqual(error.exception.has_content, bool(content.strip()))

    async def test_missing_or_whitespace_final_content_is_not_replaced_with_reasoning(self):
        for content in (None, '', ' \n\t', []):
            with self.subTest(content=content), self.assertRaises(llm.CompletionError) as error:
                await self.complete(self.response(content, reasoning_content='private reasoning trace'))
            self.assertEqual(error.exception.reason, 'empty')
            self.assertFalse(error.exception.has_content)
            self.assertNotIn('private reasoning trace', str(error.exception))
        with self.assertRaises(llm.CompletionError) as error:
            await self.complete({'choices': [{'message': {'reasoning_content': 'private reasoning trace'}}]})
        self.assertEqual(error.exception.reason, 'empty')

    async def test_refusal_or_filter_does_not_return_content(self):
        bodies = [self.response('partial answer', 'content_filter'),
                  self.response(None, refusal='private refusal details'),
                  self.response('partial answer', refusal='private refusal details')]
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(llm.CompletionError) as error:
                await self.complete(body)
            self.assertEqual(error.exception.reason, 'refusal')
            self.assertNotIn('private refusal details', str(error.exception))

    async def test_embedded_upstream_error_is_not_misreported_as_empty(self):
        with self.assertRaises(llm.CompletionError) as error:
            await self.complete({'error': {'message': 'private provider details'}})
        self.assertEqual(error.exception.reason, 'upstream_error')
        self.assertNotIn('private provider details', str(error.exception))

    async def test_invalid_json_and_response_structure_are_safely_rejected(self):
        for body in ('not json: private provider details', '<html>private provider details</html>', ''):
            with self.subTest(body=body), self.assertRaises(llm.CompletionError) as error:
                await self.complete(body, raw=True)
            self.assertEqual(error.exception.reason, 'malformed')
            self.assertNotIn('private provider details', str(error.exception))
        bodies = [None, [], 'private provider details', {}, {'choices': []}, {'choices': {}},
                  {'choices': [None]}, {'choices': [{}]}, {'choices': [{'message': []}]},
                  self.response(12), self.response({'text': 'private provider details'}),
                  self.response(['private provider details']), self.response([{'type': 'text', 'text': 12}]),
                  self.response([{'type': 'reasoning', 'text': 'private reasoning trace'}]),
                  self.response('answer', ['stop'])]
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(llm.CompletionError) as error:
                await self.complete(body)
            self.assertEqual(error.exception.reason, 'malformed')
            self.assertNotIn('private provider details', str(error.exception))
            self.assertNotIn('private reasoning trace', str(error.exception))

    async def test_other_finish_reasons_and_tool_calls_are_not_complete_answers(self):
        for finish in ('tool_calls', 'function_call', 'private provider detail'):
            with self.subTest(finish=finish), self.assertRaises(llm.CompletionError) as error:
                await self.complete(self.response('partial answer', finish))
            self.assertEqual(error.exception.reason, 'incomplete')
            self.assertTrue(error.exception.has_content)
            self.assertNotIn('private provider detail', str(error.exception))
        with self.assertRaises(llm.CompletionError) as error:
            await self.complete(self.response('partial answer', None, tool_calls=[{'id': 'test-tool'}]))
        self.assertEqual(error.exception.reason, 'incomplete')


if __name__ == '__main__':
    unittest.main()
