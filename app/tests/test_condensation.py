"""Long-document summaries must be complete, grounded, and cancellable."""
import asyncio
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import llm


def document(pages=2):
    return '\n'.join(f'[Page {page}]\n[N{page}] ' + str(page) * 13000
                     for page in range(1, pages + 1))


def page_number(messages):
    return int(re.search(r'第 (\d+)–', messages[0]['content'])[1])


class CondensationTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, text=None, *, base_url='https://model.invalid/v1',
                      model='synthetic-model', **options):
        return [event async for event in llm.condense_long_text(
            base_url, 'synthetic-key', model,
            document() if text is None else text, 5000, **options)]

    async def test_success_preserves_legacy_budget_and_source_order(self):
        async def model(base, key, name, messages, **kwargs):
            return f'Complete summary for page {page_number(messages)}'

        mocked = AsyncMock(side_effect=model)
        with patch.object(llm, 'chat_once', mocked):
            events = await self.collect()
        self.assertEqual(mocked.await_count, 2)
        self.assertEqual([call.kwargs['max_tokens'] for call in mocked.await_args_list], [4096, 4096])
        self.assertEqual([event['type'] for event in events], ['progress', 'progress', 'result'])
        self.assertLess(events[-1]['text'].index('summary for page 1'),
                        events[-1]['text'].index('summary for page 2'))

    async def test_empty_or_partial_length_retries_same_source_once_then_filters_ids(self):
        for partial in (False, True):
            with self.subTest(partial=partial):
                calls = {1: [], 2: []}

                async def model(base, key, name, messages, **kwargs):
                    page = page_number(messages)
                    calls[page].append((messages, kwargs['max_tokens']))
                    if page == 1 and len(calls[page]) == 1:
                        raise llm.CompletionError('length', 'length', has_content=partial)
                    return f'Complete summary [N{page}] invented [N99999] wrong-chunk [N{3 - page}]'

                with patch.object(llm, 'chat_once', model):
                    events = await self.collect(preserve_sources=True)
                self.assertEqual([budget for _, budget in calls[1]], [4096, 8192])
                self.assertEqual(calls[1][0][0], calls[1][1][0])
                self.assertEqual([budget for _, budget in calls[2]], [4096])
                final = events[-1]['text']
                self.assertEqual(final.count('[N1]'), 1)
                self.assertEqual(final.count('[N2]'), 1)
                self.assertNotIn('[N99999]', final)
                self.assertEqual([event['type'] for event in events], ['progress', 'progress', 'result'])

    async def test_exhausted_retry_reports_condensation_stage_and_never_yields_result(self):
        for partial in (False, True):
            with self.subTest(partial=partial):
                model = AsyncMock(side_effect=llm.CompletionError('length', 'length', has_content=partial))
                events = []
                with patch.object(llm, 'chat_once', model), self.assertRaises(llm.UpstreamError) as error:
                    async for event in llm.condense_long_text('mock', 'mock', 'mock', document(5), 5000):
                        events.append(event)
                self.assertIn('分段整理达到生成长度上限', str(error.exception))
                self.assertIn('原笔记已保留', str(error.exception))
                self.assertEqual([call.kwargs['max_tokens'] for call in model.await_args_list], [4096, 8192])
                self.assertFalse(any(event['type'] == 'result' for event in events))

    async def test_unrelated_completion_failures_are_not_retried(self):
        errors = [llm.CompletionError(reason) for reason in
                  ('empty', 'refusal', 'upstream_error', 'malformed', 'incomplete')]
        errors.extend([llm.UpstreamError('HTTP failure', status_code=400),
                       llm.UpstreamError('HTTP failure', status_code=429), TimeoutError('timeout')])
        for expected in errors:
            with self.subTest(error=type(expected).__name__, reason=getattr(expected, 'reason', '')):
                model = AsyncMock(side_effect=expected)
                with patch.object(llm, 'chat_once', model), self.assertRaises(type(expected)) as actual:
                    await self.collect(document(5))
                self.assertIs(actual.exception, expected)
                model.assert_awaited_once()

    async def test_failed_chunk_cancels_inflight_sibling_without_starting_queued_requests(self):
        started, cancelled = [], []
        both_started = asyncio.Event()

        async def model(base, key, name, messages, **kwargs):
            page = page_number(messages)
            started.append(page)
            if page == 1:
                await both_started.wait()
                raise llm.UpstreamError('synthetic failure')
            both_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.append(page)
                raise

        with patch.object(llm, 'chat_once', model), self.assertRaises(llm.UpstreamError):
            await self.collect(document(6))
        self.assertEqual(started, [1, 2])
        self.assertEqual(cancelled, [2])

    async def test_cancelling_consumer_cancels_and_awaits_all_model_requests(self):
        started, cancelled = [], []
        both_started = asyncio.Event()

        async def model(base, key, name, messages, **kwargs):
            page = page_number(messages)
            started.append(page)
            if len(started) == 2:
                both_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.append(page)
                raise

        with patch.object(llm, 'chat_once', model):
            task = asyncio.create_task(self.collect(document(6)))
            await asyncio.wait_for(both_started.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(started, [1, 2])
        self.assertEqual(sorted(cancelled), started)

    async def test_closing_after_progress_cleans_up_active_and_queued_chunks(self):
        started, cancelled = [], []
        both_started = asyncio.Event()

        async def model(base, key, name, messages, **kwargs):
            page = page_number(messages)
            started.append(page)
            if page == 1:
                await both_started.wait()
                return 'Complete first summary'
            both_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.append(page)
                raise

        with patch.object(llm, 'chat_once', model):
            events = llm.condense_long_text('mock', 'mock', 'mock', document(6), 5000)
            first = await asyncio.wait_for(anext(events), 1)
            self.assertEqual(first['type'], 'progress')
            await events.aclose()
        self.assertLessEqual(len(started), 3)
        self.assertEqual(sorted(cancelled), sorted(page for page in started if page != 1))

    async def test_small_document_needs_no_model_request(self):
        model = AsyncMock(side_effect=AssertionError('Unexpected model request'))
        with patch.object(llm, 'chat_once', model):
            events = await self.collect('[Page 1]\nShort text [N1]', preserve_sources=True)
        model.assert_not_awaited()
        self.assertEqual(events, [{'type': 'result', 'text': '[Page 1]\nShort text [N1]'}])

    async def test_official_glm53_condensation_and_retry_use_low_reasoning(self):
        for base, name in (
            ('https://open.bigmodel.cn/api/paas/v4', 'glm-5.3'),
            ('https://open.bigmodel.cn/api/paas/v4/', 'glm-5.3-flash'),
            ('https://OPEN.BIGMODEL.CN:443/api/paas/v4/chat/completions', 'GLM-5.3-Flash'),
        ):
            with self.subTest(base=base, model=name):
                first = True

                async def model(*args, **kwargs):
                    nonlocal first
                    if first:
                        first = False
                        raise llm.CompletionError('length', 'length', has_content=True)
                    return 'Complete summary'

                mocked = AsyncMock(side_effect=model)
                with patch.object(llm, 'chat_once', mocked):
                    result = await self.collect(base_url=base, model=name)
                self.assertEqual(result[-1]['type'], 'result')
                self.assertEqual(mocked.await_count, 3)
                self.assertEqual([call.kwargs['max_tokens'] for call in mocked.await_args_list],
                                 [16384, 32768, 16384])
                self.assertTrue(all(call.kwargs['reasoning_effort'] == 'low'
                                    for call in mocked.await_args_list))

    async def test_unknown_models_endpoints_and_proxies_keep_original_parameters(self):
        official = 'https://open.bigmodel.cn/api/paas/v4'
        for base, name in (
            (official, 'glm-5.2'), (official, 'glm-5.3-preview'),
            (official, 'glm-5.3-flash-extra'), (official, 'zai-org/glm-5.3-flash'),
            (official, ' glm-5.3'), (official, 'another-model'),
            ('https://model.example/api/paas/v4', 'glm-5.3-flash'),
            ('https://open.bigmodel.cn.example/api/paas/v4', 'glm-5.3-flash'),
            ('https://open.bigmodel.cn:8443/api/paas/v4', 'glm-5.3-flash'),
            ('https://open.bigmodel.cn/api/coding/paas/v4', 'glm-5.3-flash'),
            ('http://open.bigmodel.cn/api/paas/v4', 'glm-5.3-flash'),
            ('https://open.bigmodel.cn/api/paas/v4/chat/completions?variant=test', 'glm-5.3-flash'),
        ):
            with self.subTest(base=base, model=name):
                mocked = AsyncMock(return_value='Complete summary')
                with patch.object(llm, 'chat_once', mocked):
                    result = await self.collect(base_url=base, model=name)
                self.assertEqual(result[-1]['type'], 'result')
                for call in mocked.await_args_list:
                    self.assertEqual(call.kwargs, {'max_tokens': 4096})


if __name__ == '__main__':
    unittest.main()
