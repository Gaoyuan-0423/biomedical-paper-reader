"""Persist model observations independently of paper notes; use synthetic PDFs only."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from test_documents import TestClient, fixture_pdf, main


MARKDOWN = '''## Panel observations

- **Panel A:** the synthetic control is higher.
- Panel B remains *uncertain*; do not infer significance.

| Group | Visible value |
| --- | --- |
| Control | 2 |
| Test | 1 |

```text
Keep <raw> model text and line breaks.
```

[Method](https://example.org/method)

<img src=x onerror="window.synthetic=true">
'''.strip()


def sse_events(response):
    return [json.loads(line[5:]) for line in response.text.splitlines()
            if line.startswith('data:')]


class FigureObservationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='bioreader-observations-')
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        settings = {**main.DEFAULT_SETTINGS, 'base_url': 'https://model.invalid/v1',
                    'api_key': 'synthetic-not-a-secret', 'vision_model': 'synthetic-vision'}
        for name, value in [('DATA_DIR', directory), ('PAPERS_DIR', directory / 'papers'),
                            ('SETTINGS_FILE', directory / 'settings.json')]:
            patcher = patch.object(main, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        settings_patch = patch.object(main, 'load_settings', return_value=settings)
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        # A test that forgets its model stub must fail instead of reaching a network.
        model_patch = patch.object(main, 'chat_once', AsyncMock(
            side_effect=AssertionError('No real model requests in observation tests')))
        model_patch.start()
        self.addCleanup(model_patch.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        response = self.client.post('/api/papers', files={
            'file': ('synthetic-main.pdf', fixture_pdf(), 'application/pdf')})
        self.assertEqual(response.status_code, 200, response.text)
        self.url = '/api/papers/' + response.json()['id']
        self.directory = main.paper_dir(response.json()['id'])
        response = self.client.post(self.url + '/supplements', files={
            'file': ('synthetic-supplement.pdf', fixture_pdf('Supplementary Figure S1', 'SUPPLEMENT'),
                     'application/pdf')})
        self.assertEqual(response.status_code, 200, response.text)
        self.figures = self.client.get(self.url + '/figures').json()
        self.assertEqual(len(self.figures), 2)
        self.main_figure = next(f for f in self.figures if f['document_id'] == 'main')
        self.supplement = next(f for f in self.figures if f['document_id'] != 'main')
        self.notes = '# My existing reading note\n\n**Keep my wording.**\n\n![Figure](figure:' + self.main_figure['id'] + ')'
        response = self.client.patch(self.url, json={'notes': self.notes})
        self.assertEqual(response.status_code, 200, response.text)
        self.meta_before = (self.directory / 'meta.json').read_bytes()

    def read_single(self, figure, question=''):
        return self.client.post(self.url + '/figures/' + figure['id'] + '/read', json={'question': question})

    def seed(self, figure, observation):
        response = self.client.patch(self.url + '/figures/' + figure['id'], json={'observation': observation})
        self.assertEqual(response.status_code, 200, response.text)

    def assert_reopened(self, expected):
        # The public reopen path is disk-backed: a new client obtains the paper
        # and figures independently, without asking a model to regenerate them.
        with TestClient(main.app) as reopened:
            paper = reopened.get(self.url).json()
            records = reopened.get(self.url + '/figures').json()
        self.assertEqual(paper['notes'], self.notes)
        self.assertEqual((self.directory / 'meta.json').read_bytes(), self.meta_before)
        self.assertEqual(records, json.loads((self.directory / 'figures.json').read_text('utf-8')))
        for fid, observation in expected.items():
            self.assertEqual(next(f for f in records if f['id'] == fid)['observation'], observation)
        return records

    def test_single_saves_complete_markdown_and_reopening_restores_it_without_rewriting_notes(self):
        model = AsyncMock(return_value='\n' + MARKDOWN + '\n')
        with patch.object(main, 'chat_once', model):
            response = self.read_single(self.main_figure, 'Compare the two synthetic groups.')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['observation'], MARKDOWN)
            records = self.assert_reopened({self.main_figure['id']: MARKDOWN})
        model.assert_awaited_once()
        prompt = model.await_args.args[3][0]['content']
        self.assertIn('Compare the two synthetic groups.', prompt[0]['text'])
        self.assertIn(self.main_figure['caption'], prompt[0]['text'])
        self.assertTrue(prompt[1]['image_url']['url'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(next(f for f in records if f['id'] == self.supplement['id']), self.supplement)
        self.assertEqual({k: v for k, v in response.json().items() if k != 'observation'},
                         {k: v for k, v in self.main_figure.items() if k != 'observation'})

    def test_single_reread_replaces_only_target_observation_and_keeps_other_saved_observations(self):
        self.seed(self.main_figure, 'Previous main observation')
        self.seed(self.supplement, '## Saved supplementary observation')
        model = AsyncMock(return_value=MARKDOWN)
        with patch.object(main, 'chat_once', model):
            response = self.read_single(self.main_figure, 'Inspect Panel A again.')
        self.assertEqual(response.status_code, 200, response.text)
        model.assert_awaited_once()
        self.assert_reopened({self.main_figure['id']: MARKDOWN,
                              self.supplement['id']: '## Saved supplementary observation'})

    def test_batch_persists_main_and_supplement_markdown_and_reuses_saved_cache(self):
        async def model(*args, **kwargs):
            prompt = args[3][0]['content'][0]['text']
            return MARKDOWN + ('\n\nSupplementary result.' if 'SUPPLEMENT' in prompt else '\n\nMain result.')

        mock = AsyncMock(side_effect=model)
        with patch.object(main, 'chat_once', mock):
            first = self.client.post(self.url + '/figures/read')
            events = sse_events(first)
            self.assertEqual([e['type'] for e in events], ['start', 'stage', 'stage', 'done'])
            self.assertEqual({e['figure'] for e in events if e['type'] == 'stage'},
                             {self.main_figure['id'], self.supplement['id']})
            records = self.assert_reopened({self.main_figure['id']: MARKDOWN + '\n\nMain result.',
                                          self.supplement['id']: MARKDOWN + '\n\nSupplementary result.'})
            self.assertEqual(events[-1]['figures'], records)
            second = sse_events(self.client.post(self.url + '/figures/read'))
            self.assertEqual([e['type'] for e in second], ['start', 'done'])
            self.assertEqual(second[-1]['figures'], records)
        self.assertEqual(mock.await_count, 2)

    def test_batch_only_reads_missing_cache_and_force_refreshes_all_selected_figures(self):
        self.seed(self.main_figure, 'Already saved')
        model = AsyncMock(return_value=MARKDOWN)
        with patch.object(main, 'chat_once', model):
            events = sse_events(self.client.post(self.url + '/figures/read'))
            self.assertEqual([e['figure'] for e in events if e['type'] == 'stage'], [self.supplement['id']])
            model.assert_awaited_once()
            self.assert_reopened({self.main_figure['id']: 'Already saved', self.supplement['id']: MARKDOWN})
            events = sse_events(self.client.post(self.url + '/figures/read?force=1'))
            self.assertEqual(events[-1]['type'], 'done')
            self.assertEqual(model.await_count, 3)
        self.assert_reopened({self.main_figure['id']: MARKDOWN, self.supplement['id']: MARKDOWN})

    def test_single_model_failure_or_transport_timeout_keeps_prior_observations_and_notes(self):
        self.seed(self.main_figure, MARKDOWN)
        self.seed(self.supplement, 'Untouched supplement')
        before = (self.directory / 'figures.json').read_bytes()
        cases = [(main.UpstreamError('Synthetic rate limit', status_code=429), 400, 'Synthetic rate limit'),
                 (httpx.ReadTimeout('SENSITIVE synthetic transport URL'), 504, '读图请求超时，请稍后重试。'),
                 (httpx.ConnectError('SENSITIVE synthetic transport URL'), 502,
                  '无法连接读图模型，请检查网络或服务地址后重试。')]
        for error, status, detail in cases:
            with self.subTest(error=type(error).__name__):
                model = AsyncMock(side_effect=error)
                with patch.object(main, 'chat_once', model):
                    response = self.read_single(self.main_figure)
                self.assertEqual(response.status_code, status, response.text)
                self.assertEqual(response.json()['detail'], detail)
                self.assertNotIn('SENSITIVE', response.text)
                model.assert_awaited_once()
                self.assertEqual((self.directory / 'figures.json').read_bytes(), before)
                self.assert_reopened({self.main_figure['id']: MARKDOWN,
                                      self.supplement['id']: 'Untouched supplement'})

    def test_batch_failure_emits_terminal_error_and_retains_completed_and_prior_observations(self):
        self.seed(self.main_figure, 'Previous main observation')
        self.seed(self.supplement, 'Previous supplement observation')
        cases = [main.UpstreamError('Synthetic rate limit', status_code=429),
                 httpx.ReadTimeout('SENSITIVE synthetic transport URL'),
                 httpx.ConnectError('SENSITIVE synthetic transport URL')]
        for error in cases:
            with self.subTest(error=type(error).__name__):
                saved = asyncio.Event()
                original_save = main.save_figures

                def save(directory, records):
                    original_save(directory, records)
                    saved.set()

                async def model(*args, **kwargs):
                    if 'SUPPLEMENT' in args[3][0]['content'][0]['text']:
                        await asyncio.wait_for(saved.wait(), timeout=2)
                        raise error
                    return MARKDOWN

                mock = AsyncMock(side_effect=model)
                with patch.object(main, 'chat_once', mock), patch.object(main, 'save_figures', save):
                    response = self.client.post(self.url + '/figures/read?force=1')
                self.assertEqual(response.status_code, 200, response.text)
                events = sse_events(response)
                self.assertEqual([e['type'] for e in events], ['start', 'stage', 'error'])
                self.assertEqual(events[1]['figure'], self.main_figure['id'])
                self.assertTrue(events[-1]['message'])
                self.assertNotIn('SENSITIVE', response.text)
                self.assertEqual(mock.await_count, 2)  # No implicit rerun of an expensive model request.
                self.assert_reopened({self.main_figure['id']: MARKDOWN,
                                      self.supplement['id']: 'Previous supplement observation'})

    def test_batch_timeout_before_progress_keeps_cache_and_does_not_retry_or_report_success(self):
        self.seed(self.main_figure, MARKDOWN)
        self.seed(self.supplement, 'Untouched supplement')
        before = (self.directory / 'figures.json').read_bytes()
        settings = {**main.load_settings(), 'max_figures': 1}
        model = AsyncMock(side_effect=httpx.ReadTimeout('SENSITIVE synthetic transport URL'))
        with patch.object(main, 'load_settings', return_value=settings), patch.object(main, 'chat_once', model):
            events = sse_events(self.client.post(self.url + '/figures/read?force=1'))
            self.assertEqual([event['type'] for event in events], ['start', 'error'])
            self.assertEqual(events[-1]['message'], '读图请求超时，请稍后重试。')
            self.assertEqual((self.directory / 'figures.json').read_bytes(), before)
            cached = sse_events(self.client.post(self.url + '/figures/read'))
            self.assertEqual([event['type'] for event in cached], ['start', 'done'])
            model.assert_awaited_once()
        self.assert_reopened({self.main_figure['id']: MARKDOWN,
                              self.supplement['id']: 'Untouched supplement'})

    def test_model_result_merges_without_reverting_concurrent_captions_new_crops_or_other_observations(self):
        settings = {**main.load_settings(), 'max_figures': 1}
        for mode in ('single', 'batch'):
            with self.subTest(mode=mode):
                changed = {}

                async def model(*args, **kwargs):
                    paper_id = self.directory.name
                    main.patch_figure(paper_id, self.main_figure['id'], main.FigurePatch(
                        caption='New human caption saved while reading'))
                    main.patch_figure(paper_id, self.supplement['id'], main.FigurePatch(
                        observation='A newer observation on another figure'))
                    crop = main.crop_manual_figure(paper_id, main.CropIn(
                        document_id='main', page=1, bbox=[65, 240, 200, 360], label='Concurrent crop'))
                    changed['crop'] = crop
                    changed['records'] = main.load_figures(self.directory)
                    self.meta_before = (self.directory / 'meta.json').read_bytes()
                    return MARKDOWN

                with patch.object(main, 'load_settings', return_value=settings), patch.object(main, 'chat_once', model):
                    response = (self.read_single(self.main_figure) if mode == 'single'
                                else self.client.post(self.url + '/figures/read?force=1'))
                self.assertEqual(response.status_code, 200, response.text)
                if mode == 'batch':
                    self.assertEqual(sse_events(response)[-1]['type'], 'done')
                after = self.assert_reopened({self.main_figure['id']: MARKDOWN,
                                             self.supplement['id']: 'A newer observation on another figure'})
                expected = changed['records']
                next(f for f in expected if f['id'] == self.main_figure['id'])['observation'] = MARKDOWN
                self.assertEqual(after, expected)
                self.assertIn(changed['crop'], after)
                if mode == 'single':
                    self.assertEqual(response.json()['caption'], 'New human caption saved while reading')

    def test_deleted_figure_is_not_resurrected_by_late_single_or_batch_result(self):
        settings = {**main.load_settings(), 'max_figures': 1}
        image_path = self.directory / 'figures' / (self.main_figure['id'] + '.png')
        image_bytes = image_path.read_bytes()
        for mode in ('single', 'batch'):
            with self.subTest(mode=mode):
                main.save_figures(self.directory, self.figures)
                image_path.write_bytes(image_bytes)
                changed = {}

                async def model(*args, **kwargs):
                    main.delete_figure(self.directory.name, self.main_figure['id'])
                    changed['records'] = (self.directory / 'figures.json').read_bytes()
                    self.meta_before = (self.directory / 'meta.json').read_bytes()
                    return MARKDOWN

                with patch.object(main, 'load_settings', return_value=settings), patch.object(main, 'chat_once', model):
                    response = (self.read_single(self.main_figure) if mode == 'single'
                                else self.client.post(self.url + '/figures/read?force=1'))
                if mode == 'single':
                    self.assertEqual(response.status_code, 409, response.text)
                else:
                    self.assertEqual([e['type'] for e in sse_events(response)], ['start', 'error'])
                self.assertIn('图片已删除或内容已更新', response.text)
                self.assertEqual((self.directory / 'figures.json').read_bytes(), changed['records'])
                self.assertFalse(image_path.exists())
                self.assertNotIn(self.main_figure['id'], {f['id'] for f in self.assert_reopened({})})

    def test_changed_source_or_newer_target_observation_rejects_late_results_without_rolling_back_records(self):
        settings = {**main.load_settings(), 'max_figures': 1}
        baseline_meta = self.meta_before
        for mode in ('single', 'batch'):
            for change in ('page', 'bbox', 'document_id', 'revision', 'observation'):
                with self.subTest(mode=mode, change=change):
                    (self.directory / 'meta.json').write_bytes(baseline_meta)
                    main.save_figures(self.directory, self.figures)
                    changed = {}

                    async def model(*args, **kwargs):
                        records = main.load_figures(self.directory)
                        target = next(f for f in records if f['id'] == self.main_figure['id'])
                        if change == 'revision':
                            meta = main.read_meta(self.directory)
                            meta['document_revisions']['main'] = 'new-synthetic-document-revision'
                            main.write_meta(self.directory, meta)
                        elif change == 'page':
                            target['page'] += 1
                        elif change == 'bbox':
                            target['bbox'] = [coordinate + 1 for coordinate in target['bbox']]
                        elif change == 'document_id':
                            target['document_id'] = self.supplement['document_id']
                        else:
                            target['observation'] = 'A newer human observation on this figure'
                        main.save_figures(self.directory, records)
                        changed['records'] = (self.directory / 'figures.json').read_bytes()
                        self.meta_before = (self.directory / 'meta.json').read_bytes()
                        return MARKDOWN

                    with patch.object(main, 'load_settings', return_value=settings), patch.object(main, 'chat_once', model):
                        response = (self.read_single(self.main_figure) if mode == 'single'
                                    else self.client.post(self.url + '/figures/read?force=1'))
                    if mode == 'single':
                        self.assertEqual(response.status_code, 409, response.text)
                    else:
                        self.assertEqual([e['type'] for e in sse_events(response)], ['start', 'error'])
                    self.assertEqual((self.directory / 'figures.json').read_bytes(), changed['records'])
                    self.assert_reopened({})


if __name__ == '__main__':
    unittest.main()
