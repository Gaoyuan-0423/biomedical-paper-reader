"""Chat must not mistake a truncated upstream SSE response for completion."""
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


if __name__ == '__main__':
    unittest.main()
