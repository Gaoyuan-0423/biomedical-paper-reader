"""Persist model observations independently of paper notes; use synthetic PDFs only."""
import asyncio
import json
import shutil
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
        self.assertEqual(response.json()['observation_language'], 'zh-CN')
        self.assertEqual({k: v for k, v in response.json().items() if k not in ('observation', 'observation_language')},
                         {k: v for k, v in self.main_figure.items() if k not in ('observation', 'observation_language')})

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
                next(f for f in expected if f['id'] == self.main_figure['id']).update(observation=MARKDOWN, observation_language='zh-CN')
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

    def test_empty_model_results_never_clear_saved_observations_or_claim_batch_success(self):
        self.seed(self.main_figure, MARKDOWN)
        settings = {**main.load_settings(), 'max_figures': 1}
        before = (self.directory / 'figures.json').read_bytes()
        for result in ('', '\n \t'):
            for mode in ('single', 'batch'):
                with self.subTest(result=repr(result), mode=mode):
                    with patch.object(main, 'chat_once', AsyncMock(return_value=result)), patch.object(main, 'load_settings', return_value=settings):
                        response = (self.read_single(self.main_figure) if mode == 'single'
                                    else self.client.post(self.url + '/figures/read?force=1'))
                    if mode == 'single':
                        self.assertEqual(response.status_code, 400, response.text)
                    else:
                        self.assertEqual([e['type'] for e in sse_events(response)], ['start', 'error'])
                    self.assertIn('读图模型没有返回有效内容', response.text)
                    self.assertEqual((self.directory / 'figures.json').read_bytes(), before)
                    self.assert_reopened({self.main_figure['id']: MARKDOWN})

    def test_explicit_all_reads_beyond_budget_and_cached_figures_without_changing_automatic_note_budget(self):
        records = main.load_figures(self.directory)
        for index in range(3, 12):
            new = dict(self.main_figure, id=f'f{index:03d}', page=index, observation='')
            records.append(new)
            shutil.copy2(self.directory / 'figures' / (self.main_figure['id'] + '.png'),
                         self.directory / 'figures' / (new['id'] + '.png'))
        main.save_figures(self.directory, records)
        selected = sorted(records, key=lambda f: (f['document_id'] != 'main', f['document_id'], f['page'], f['id']))
        for figure in selected[:8]:
            self.seed(figure, 'Already saved')
        settings = {**main.load_settings(), 'max_figures': 8}
        model = AsyncMock(return_value=MARKDOWN)
        with patch.object(main, 'load_settings', return_value=settings), patch.object(main, 'chat_once', model):
            limited = sse_events(self.client.post(self.url + '/figures/read'))
            self.assertEqual([e['type'] for e in limited], ['start', 'done'])
            model.assert_not_awaited()
            full = sse_events(self.client.post(self.url + '/figures/read?scope=all'))
            self.assertEqual(full[-1]['type'], 'done')
            self.assertEqual({e['figure'] for e in full if e['type'] == 'stage'}, {f['id'] for f in selected[8:]})
            self.assertEqual(model.await_count, len(selected) - 8)
            _, automatic = main._figures_to_read(self.directory, settings, True)
            self.assertEqual([f['id'] for f in automatic], [f['id'] for f in selected[:8]])
            repeated = sse_events(self.client.post(self.url + '/figures/read?scope=all'))
            self.assertEqual([e['type'] for e in repeated], ['start', 'done'])
            self.assertEqual(model.await_count, len(selected) - 8)
        self.assert_reopened({f['id']: ('Already saved' if f in selected[:8] else MARKDOWN) for f in selected})

    def test_unrecoverable_storage_returns_error_and_never_overwrites_original_files(self):
        primary, backup = self.directory / 'figures.json', self.directory / 'figures.json.bak'
        primary.write_bytes(b'[{"id":"f001","observation":')
        backup.write_bytes(b'broken backup')
        original = primary.read_bytes()
        for method, endpoint, body in [('get', '/figures', None),
                                       ('patch', '/figures/' + self.main_figure['id'], {'caption': 'New caption'}),
                                       ('post', '/figures/' + self.main_figure['id'] + '/read', {})]:
            with self.subTest(endpoint=endpoint):
                response = getattr(self.client, method)(self.url + endpoint, **({'json': body} if body is not None else {}))
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn('图片记录文件损坏或无法读取', response.text)
                self.assertEqual(primary.read_bytes(), original)
                self.assertEqual(backup.read_bytes(), b'broken backup')
                self.assertEqual((self.directory / 'meta.json').read_bytes(), self.meta_before)

    def test_language_specific_batch_cache_reuses_legacy_chinese_but_explicit_english_rereads(self):
        for figure in self.figures:
            self.seed(figure, '原先保存的中文观察')
        model = AsyncMock(return_value='## Observations\n\nThe synthetic groups differ.')
        with patch.object(main, 'chat_once', model):
            legacy = sse_events(self.client.post(self.url + '/figures/read?scope=all'))
            self.assertEqual([event['type'] for event in legacy], ['start', 'done'])
            model.assert_not_awaited()
            english = sse_events(self.client.post(self.url + '/figures/read?scope=all&language=en'))
            self.assertEqual(english[-1]['type'], 'done')
            self.assertEqual(model.await_count, len(self.figures))
            for call in model.await_args_list:
                prompt = call.args[3][0]['content'][0]['text']
                self.assertIn('Output language: English', prompt)
                self.assertNotIn('用中文、Markdown', prompt)
            repeated = sse_events(self.client.post(self.url + '/figures/read?scope=all&language=en'))
            self.assertEqual([event['type'] for event in repeated], ['start', 'done'])
            self.assertEqual(model.await_count, len(self.figures))
        records = self.assert_reopened({f['id']: '## Observations\n\nThe synthetic groups differ.' for f in self.figures})
        self.assertTrue(all(f['observation_language'] == 'en' for f in records))
        _, chinese_pending = main._figures_to_read(self.directory, main.load_settings(), False, all_figures=True)
        self.assertEqual(len(chinese_pending), len(self.figures))

    def test_single_language_survives_reextraction_and_human_edits_but_clears_with_empty_observation(self):
        model = AsyncMock(return_value='## English observation\n\nAn unchanged synthetic finding.')
        with patch.object(main, 'chat_once', model):
            response = self.client.post(self.url + '/figures/' + self.main_figure['id'] + '/read',
                                        json={'question': 'Explain Panel A.', 'language': 'en'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['observation_language'], 'en')
        self.assertIn('Output language: English', model.await_args.args[3][0]['content'][0]['text'])
        response = self.client.post(self.url + '/figures/extract')
        record = next(f for f in response.json()['figures'] if f['id'] == self.main_figure['id'])
        self.assertEqual(record['observation_language'], 'en')
        self.assertEqual(record['observation'], '## English observation\n\nAn unchanged synthetic finding.')
        edited = self.client.patch(self.url + '/figures/' + record['id'], json={'observation': 'My revised English interpretation'}).json()
        self.assertEqual(edited['observation_language'], 'en')
        cleared = self.client.patch(self.url + '/figures/' + record['id'], json={'observation': ''}).json()
        self.assertNotIn('observation_language', cleared)
        self.assertEqual(cleared['observation'], '')

    def test_english_deep_and_quick_generation_uses_explicit_language_and_keeps_generated_figure_labels_english(self):
        settings = {**main.load_settings(), 'vision_model': ''}
        for mode in ('deep', 'quick'):
            with self.subTest(mode=mode):
                captured = []

                async def model(*args, **kwargs):
                    captured.extend(args[3])
                    yield '## Summary\n\nFigure 1 shows a synthetic group comparison.'

                with patch.object(main, 'load_settings', return_value=settings), patch.object(main, 'stream_chat', model):
                    response = self.client.post(self.url + '/read?mode=' + mode + '&language=en')
                events = sse_events(response)
                self.assertEqual(events[-1]['type'], 'done', events)
                self.assertIn('Output language: English', captured[0]['content'])
                self.assertNotIn('写中文阅读报告', captured[0]['content'])
                saved = self.client.get(self.url).json()
                self.assertEqual(saved['notes_language'], 'en')
                self.assertNotIn('原文图集', saved['notes'])
                self.assertNotIn(' · 正文', saved['notes'])
                self.assertIn('Main text', saved['notes'])
                edited = self.client.patch(self.url, json={'notes': saved['notes'] + '\n\nMy own edit.'}).json()
                self.assertEqual(edited['notes_language'], 'en')

    def test_unsupported_output_language_is_rejected_without_model_calls_or_mutation(self):
        before = (self.directory / 'figures.json').read_bytes()
        endpoints = [('/figures/' + self.main_figure['id'] + '/read', {'language': 'fr'}),
                     ('/figures/read?scope=all&language=fr', None), ('/read?language=fr', None)]
        for endpoint, body in endpoints:
            response = self.client.post(self.url + endpoint, **({'json': body} if body is not None else {}))
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual((self.directory / 'figures.json').read_bytes(), before)
        self.assertEqual((self.directory / 'meta.json').read_bytes(), self.meta_before)

    def test_failed_or_cancelled_all_figure_run_cancels_inflight_requests_and_does_not_drain_queue(self):
        records = main.load_figures(self.directory)
        for index in range(3, 11):
            record = dict(self.main_figure, id=f'f{index:03d}', page=index, observation='')
            records.append(record)
            shutil.copy2(self.directory / 'figures' / (self.main_figure['id'] + '.png'),
                         self.directory / 'figures' / (record['id'] + '.png'))
        main.save_figures(self.directory, records)
        before = (self.directory / 'figures.json').read_bytes()

        async def run(cancel):
            started, stopped = [], []
            both_started = asyncio.Event()

            async def model(*args, **kwargs):
                number = len(started)
                started.append(number)
                if len(started) == 2:
                    both_started.set()
                try:
                    if number == 0 and not cancel:
                        await both_started.wait()
                        raise main.UpstreamError('Synthetic first-figure failure')
                    await asyncio.sleep(30)
                    return 'Must never be saved'
                except asyncio.CancelledError:
                    stopped.append(number)
                    raise

            async def consume():
                return [event async for event in main._read_figures_events(
                    main.load_settings(), self.directory, False, all_figures=True)]

            with patch.object(main, 'chat_once', model):
                task = asyncio.create_task(consume())
                await asyncio.wait_for(both_started.wait(), timeout=2)
                if cancel:
                    task.cancel()
                try:
                    await task
                    self.fail('The failed/cancelled batch must not succeed')
                except (main.UpstreamError, asyncio.CancelledError):
                    pass
                count = len(started)
                await asyncio.sleep(0.01)
                self.assertEqual(len(started), count)
                self.assertLess(count, len(records))
                self.assertGreaterEqual(len(stopped), 1)
                self.assertEqual(len(stopped), count if cancel else count - 1)

        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                asyncio.run(run(cancel))
                self.assertEqual((self.directory / 'figures.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
