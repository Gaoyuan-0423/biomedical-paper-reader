"""Offline coverage of repeatable creation-date windows, local paging and startup catch-up."""
import asyncio
import json
import re
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from test_documents import main, TestClient
from fastapi import HTTPException


class FollowupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='bioreader-pubmed-followups-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.enterContext(patch.object(main, 'DATA_DIR', self.root))
        self.client = self.enterContext(TestClient(main.app))
        self.scope = self.client.post('/api/project-libraries', json={'name': 'Tests'}).json()['id']
        self.base = '/api/pubmed-searches/' + self.scope
        self.record = self.client.post(self.base, json={'query': 'review[pt]'}).json()
        self.url = self.base + '/' + self.record['id']
        self.path = main.sp.path_for(self.root, self.scope, self.record['id'])
        self.t = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
        self.clock = self.enterContext(patch.object(main.sp, 'now', return_value=self.t))

    def test_v1_migration_preserves_seen_last_page_backlog_and_success_schedule(self):
        legacy = {'version': 1, 'id': self.record['id'], 'query': 'review[pt]', 'seen': ['111'],
                  'latest': ['111','112'], 'total': 480, 'result_id': 'a'*64}
        self.path.write_text(json.dumps(legacy))
        self.assertEqual(self.client.get(self.url).json()['unseen'], ['112'])
        self.assertEqual(self.client.get('/api/pubmed-followups').json(), [{'scope': self.scope, 'id': self.record['id']}])
        additions = ['111','112'] + [str(i) for i in range(200, 320)]
        with patch.object(main.sp, 'search', AsyncMock(return_value=(len(additions), additions))) as search:
            first = self.client.post(self.url + '/refresh?due_only=true').json()
            search.assert_awaited_once_with('review[pt]', date(2026, 11, 14), date(2026, 11, 20))
            self.assertEqual((first['unseen_total'], first['pages'], len(first['unseen'])), (121,3,50))
            second = self.client.get(self.url + '?page=2').json()
            self.assertTrue(set(first['unseen']).isdisjoint(second['unseen']))
            self.assertEqual(len(second['unseen']), 50)
            self.assertEqual(self.client.post(self.url + '/seen', json={'result_id': second['result_id'], 'page': 1}).status_code, 409)
            marked = self.client.post(self.url + '/seen', json={'result_id': second['result_id'], 'page': 2}).json()
            self.assertEqual((marked['unseen_total'], marked['pages'], len(marked['unseen'])), (71, 2, 21))
            self.assertEqual(self.client.get(self.url).json()['unseen'], first['unseen'])
            self.assertEqual(self.client.get('/api/pubmed-followups').json(), [])
            self.client.post(self.url + '/refresh?due_only=true')
            self.assertEqual(search.await_count, 1)
        self.clock.return_value = self.t + timedelta(days=5)
        with patch.object(main.sp, 'search', AsyncMock(return_value=(len(additions), additions))) as catchup:
            repeated = self.client.post(self.url + '/refresh?due_only=true').json()
            catchup.assert_awaited_once_with('review[pt]', date(2026,11,19), date(2026,11,25))
            self.assertEqual(repeated['unseen_total'], 71)
        disk = main.sp.read(self.path)
        self.assertEqual(disk['version'], 2)
        self.assertEqual(set(disk['seen']), {'111', *second['unseen']})

    def test_search_bisection_no_partial_commit_and_retry_single_day_above_server_limit(self):
        self.enterContext(patch.object(main.sp, 'SEARCH_LIMIT', 3))
        self.enterContext(patch.object(main.sp, 'MAX_PMID', 15))
        self.enterContext(patch.object(main.sp, 'pace_requests', AsyncMock()))
        failed = True
        terms = []

        async def fixture(url, host):
            self.assertEqual(host, main.sp.HOST)
            term = parse_qs(urlsplit(url).query)['term'][0]
            terms.append(term)
            dates = re.search(r'(\d{4}/\d{2}/\d{2}):(\d{4}/\d{2}/\d{2})\[crdt\]', term)
            uid = re.search(r'(\d+):(\d+)\[uid\]', term)
            lo, hi = (map(int, uid.groups()) if uid else (1,15))
            found = [str(i) for i in range(1, 13) if lo <= i <= hi and dates[1] <= '2026/11/20' <= dates[2]]
            if failed and uid and hi <= 7:
                raise main.sp.LookupError_('offline')
            return {'esearchresult': {'count':str(len(found)), 'idlist':found[:3]}}

        with patch.object(main.sp, '_get_json', side_effect=fixture):
            self.assertEqual(self.client.post(self.url + '/refresh').status_code, 502)
            after = self.client.get(self.url).json()
            self.assertIsNone(after['last_checked'])
            self.assertEqual(after['unseen_total'], 0)
            self.assertTrue(after['last_error'])
            self.assertEqual(self.client.get('/api/pubmed-followups').json(), [])
            self.clock.return_value = self.t + timedelta(minutes=16)
            self.assertTrue(self.client.get('/api/pubmed-followups').json())
            failed = False
            success = self.client.post(self.url + '/refresh').json()
            self.assertEqual(set(success['unseen']), {str(i) for i in range(1,13)})
            self.assertEqual(success['last_error'], '')
        self.assertTrue(any('[uid]' in term for term in terms))

    def test_silent_truncation_never_advances_existing_cursor(self):
        previous = self.t - timedelta(days=5)
        main.sp.record_result(self.path, 1, ['101'], previous)
        self.enterContext(patch.object(main.sp, 'pace_requests', AsyncMock()))
        result = {'esearchresult': {'count':'3', 'idlist':['201','202']}}
        with patch.object(main.sp, '_get_json', AsyncMock(return_value=result)):
            response = self.client.post(self.url + '/refresh')
            self.assertEqual(response.status_code, 502)
        after = self.client.get(self.url).json()
        self.assertEqual(after['last_checked'], previous.isoformat())
        self.assertEqual(after['unseen'], ['101'])

    def test_concurrent_check_preserves_midflight_seen_and_is_not_run_twice(self):
        main.sp.record_result(self.path, 1, ['301'], self.t - timedelta(days=4))

        async def run():
            entered, release = asyncio.Event(), asyncio.Event()
            async def search(*args):
                entered.set()
                await release.wait()
                return 2, ['302', '301']
            with patch.object(main.sp, 'search', side_effect=search) as remote:
                operation = asyncio.create_task(main.refresh_pubmed_query(self.scope, self.record['id'], True))
                await entered.wait()
                existing = await main.refresh_pubmed_query(self.scope, self.record['id'], False)
                self.assertTrue(existing['refreshing'])
                self.assertEqual(remote.await_count, 1)
                self.client.post(self.url + '/seen', json={'page':1,'result_id':existing['result_id']}).raise_for_status()
                release.set()
                self.assertEqual((await operation)['unseen'], ['302'])
        asyncio.run(run())

    def test_paused_and_archived_queries_and_independent_corrupt_file(self):
        self.client.post('/api/pubmed-searches/all', json={'query': 'another'})
        path = self.path.with_name('f'*32 + '.json')
        path.write_text('bad')
        self.assertEqual(len(self.client.get('/api/pubmed-followups').json()), 2)
        self.assertFalse(self.client.post(self.url + '/tracking', json={'enabled':False}).json()['tracking'])
        self.assertEqual([r['scope'] for r in self.client.get('/api/pubmed-followups').json()], ['all'])
        self.client.post(self.url + '/tracking', json={'enabled':True})
        self.client.put('/api/project-libraries/' + self.scope, json={'name':'Tests','revision':1,'archived':True})
        self.assertEqual([r['scope'] for r in self.client.get('/api/pubmed-followups').json()], ['all'])
        self.assertEqual(self.client.post(self.url + '/refresh').status_code, 409)


if __name__ == '__main__':
    unittest.main()
