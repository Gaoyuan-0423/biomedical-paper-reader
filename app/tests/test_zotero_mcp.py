"""Protocol and Zotero adapter checks using only a synthetic MCP server."""
import asyncio
import copy
import json
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import zotero_mcp as zm

PARENT = 'PAPER001'
ATTACHMENT = 'ATTACH01'
NOTE = 'NOTE0001'
TOOLS = [{'name': name, 'inputSchema': {'type': 'object'}} for name in sorted(zm.READ_TOOLS)]


def item(key=PARENT, library_id=0, **fields):
    data = {'key': key, 'itemType': 'journalArticle', 'title': '合成文献 Synthetic paper',
            'date': '2024-03-15', 'DOI': 'https://doi.org/10.1234/synthetic',
            'creators': [{'firstName': 'Ada', 'lastName': 'Test'}, {'name': 'Synthetic Group'}],
            'publicationTitle': 'Synthetic Journal', 'abstractNote': 'Test abstract, no private library.',
            'tags': [{'tag': '单细胞'}, {'tag': 'RNA'}, {'tag': 'rna'}],
            'url': 'https://example.org/paper', **fields}
    return {'key': key, 'version': 12, 'library': {'type': 'user', 'id': library_id}, 'data': data}


def search_text(number=4):
    result = ["# Search Results for 'synthetic'", '']
    for index in range(number):
        result += [f'## {index + 1}. 合成论文 {index}', '**Type:** journalArticle',
                   f'**Item Key:** TEST{index:04d}', '**Date:** 2024-01-02', '**Authors:** Ada Test',
                   '**Abstract:** Fixture only', '**Tags:** `synthetic`', '']
    return '\n'.join(result)


CHILDREN = f'''# Child Items for: Synthetic paper

## Attachments
1. **Main PDF**
   - Key: {ATTACHMENT}
   - Type: application/pdf
   - Filename: synthetic.pdf

## Notes
1. **Private note not requested**
   - Key: {NOTE}
   - Content: This note must not become an attachment.
'''


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks, delay=0):
        self.chunks, self.delay = chunks, delay

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk


class Server:
    def __init__(self, sse=False, version='2025-11-25'):
        self.requests = []
        self.sse, self.version = sse, version
        self.session = 'test-session'
        self.tools = copy.deepcopy(TOOLS)
        self.items = {PARENT: item(), ATTACHMENT: item(ATTACHMENT, itemType='attachment', parentItem=PARENT,
                                                       filename='synthetic.pdf', contentType='application/pdf',
                                                       linkMode='imported_file', path='/never/read/from/server.pdf')}
        self.children = CHILDREN
        self.search_result = None
        self.override = None
        self.initializations = 0

    def result(self, request, payload, result, headers=None):
        envelope = {'jsonrpc': '2.0', 'id': payload['id'], 'result': result}
        if not self.sse:
            return httpx.Response(200, json=envelope, headers=headers)
        notification = json.dumps({'jsonrpc': '2.0', 'method': 'notifications/progress', 'params': {'progress': 1}})
        body = (': heartbeat\r\n\r\ndata: ' + notification + '\r\n\r\ndata: ' +
                json.dumps(envelope, ensure_ascii=False) + '\r\n\r\n').encode()
        return httpx.Response(200, headers={'content-type': 'text/event-stream', **(headers or {})},
                              stream=ChunkStream([body[i:i + 7] for i in range(0, len(body), 7)]))

    async def handle(self, request):
        payload = json.loads(request.content) if request.content else {}
        self.requests.append((request, payload))
        if self.override:
            response = self.override(request, payload)
            if response is not None:
                return response
        if request.method == 'DELETE':
            return httpx.Response(204)
        method = payload.get('method')
        if method == 'initialize':
            self.initializations += 1
            return self.result(request, payload, {'protocolVersion': self.version, 'capabilities': {'tools': {}},
                                                 'serverInfo': {'name': 'Synthetic Zotero MCP', 'version': '0.0'}},
                               {'Mcp-Session-Id': self.session})
        if method == 'notifications/initialized':
            return httpx.Response(202)
        if method == 'tools/list':
            return self.result(request, payload, {'tools': self.tools})
        if method == 'tools/call':
            name = payload['params']['name']
            arguments = payload['params']['arguments']
            if name == 'zotero_search_items':
                value = self.search_result if self.search_result is not None else search_text(min(arguments['limit'], 24))
            elif name == 'zotero_get_item_metadata':
                self.assert_format(arguments)
                record = self.items.get(arguments['item_key'])
                value = json.dumps(record, ensure_ascii=False) if record else 'No item found with key: ' + arguments['item_key']
            elif name == 'zotero_get_item_children':
                value = self.children
            else:
                raise AssertionError('Unexpected tool ' + name)
            return self.result(request, payload, {'content': [{'type': 'text', 'text': value}]})
        raise AssertionError('Unexpected request ' + repr(payload))

    @staticmethod
    def assert_format(arguments):
        if arguments.get('format') != 'json':
            raise AssertionError('Metadata must use JSON, never parse human citation text')

    def client(self, endpoint='http://127.0.0.1:8000/mcp', bearer='synthetic-token'):
        return zm.ZoteroMCPClient(endpoint, bearer, transport=httpx.MockTransport(self.handle))


class ZoteroMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_and_fragmented_sse_handshake_session_and_read_only_discovery(self):
        for sse, version in ((False, '2025-11-25'), (True, '2025-06-18')):
            with self.subTest(sse=sse):
                server = Server(sse, version)
                server.tools.append({'name': 'zotero_delete_items', 'annotations': {'readOnlyHint': True}})
                async with server.client() as client:
                    discovered = await client.discover()
                    self.assertEqual(discovered['protocol_version'], version)
                    self.assertEqual(discovered['capabilities'], {'search': True, 'metadata': True, 'attachments': True})
                    self.assertNotIn('zotero_delete_items', discovered['tools'])
                    with self.assertRaises(zm.MCPError) as caught:
                        await client._call_tool('zotero_delete_items', {})
                    self.assertEqual(caught.exception.status_code, 400)
                self.assertEqual(server.requests[0][1]['params']['capabilities'], {})
                self.assertEqual(server.requests[1][1]['method'], 'notifications/initialized')
                for request, payload in server.requests:
                    self.assertEqual(str(request.url), 'http://127.0.0.1:8000/mcp')
                    self.assertEqual(request.headers.get('authorization'), 'Bearer synthetic-token')
                    if payload.get('method') != 'initialize':
                        self.assertEqual(request.headers.get('Mcp-Session-Id'), server.session)
                        self.assertEqual(request.headers.get('MCP-Protocol-Version'), version)
                self.assertEqual(server.requests[-1][0].method, 'DELETE')

    async def test_search_real_markdown_prefix_pagination_and_window_end(self):
        server = Server()
        async with server.client() as client:
            first = await client.search('合成', limit=10)
            second = await client.search('合成', limit=10, start=10)
            self.assertEqual(first['items'][0]['key'], 'TEST0000')
            self.assertEqual(second['items'][0]['key'], 'TEST0010')
            self.assertTrue(first['has_more'] and second['has_more'])
            self.assertEqual(second['items'][0]['year'], '2024')
            server.search_result = search_text(100)
            last = await client.search('合成', limit=20, start=90)
            self.assertEqual(len(last['items']), 10)
            self.assertFalse(last['has_more'])
        calls = [payload['params']['arguments'] for _, payload in server.requests if payload.get('method') == 'tools/call']
        self.assertEqual([call['limit'] for call in calls], [11, 21, 100])
        self.assertTrue(all('start' not in call and call['search_all_libraries'] is False for call in calls))

    async def test_metadata_and_attachments_are_revalidated_without_paths_or_notes(self):
        server = Server(sse=True)
        async with server.client() as client:
            parent = await client.get_item(PARENT)
            self.assertEqual(parent['doi'], '10.1234/synthetic')
            self.assertEqual(parent['tags'], ['单细胞', 'RNA'])
            self.assertEqual(parent['authors'], 'Ada Test; Synthetic Group')
            self.assertEqual((parent['library_type'], parent['library_id']), ('user', 0))
            attachments = await client.get_attachments(PARENT, parent=parent)
            self.assertEqual(attachments, [{'key': ATTACHMENT, 'parent_key': PARENT, 'title': parent['title'],
                                           'filename': 'synthetic.pdf', 'content_type': 'application/pdf',
                                           'link_mode': 'imported_file', 'library_type': 'user', 'library_id': 0}])
            self.assertNotIn('/never/read', json.dumps(attachments))
        requested = [payload['params']['arguments'].get('item_key') for _, payload in server.requests if payload.get('method') == 'tools/call']
        self.assertEqual(requested, [PARENT, PARENT, ATTACHMENT])
        self.assertNotIn(NOTE, requested)

    async def test_attachment_wrong_parent_or_library_rejected_and_unknown_identity_preserved(self):
        for change in ('parent', 'library', 'unknown'):
            with self.subTest(change=change):
                server = Server()
                if change == 'parent':
                    server.items[ATTACHMENT]['data']['parentItem'] = 'OTHER001'
                elif change == 'library':
                    server.items[ATTACHMENT]['library']['id'] = 99
                else:
                    del server.items[ATTACHMENT]['library']
                async with server.client() as client:
                    if change == 'unknown':
                        attachment = (await client.get_attachments(PARENT))[0]
                        self.assertIsNone(attachment['library_id'])
                        self.assertEqual(attachment['library_type'], '')
                    else:
                        with self.assertRaises(zm.MCPError):
                            await client.get_attachments(PARENT)

    async def test_explicit_empty_results_not_found_and_malformed_results_are_distinct(self):
        server = Server()
        async with server.client() as client:
            server.search_result = "No items found matching query: 'empty'"
            self.assertEqual(await client.search('empty'), {'items': [], 'has_more': False})
            for bad in ('Error searching Zotero: secret-credential', 'Unexpected format', search_text(1).replace('TEST0000', '../bad')):
                server.search_result = bad
                with self.assertRaises(zm.MCPError) as caught:
                    await client.search('empty')
                self.assertNotIn('secret-credential', caught.exception.message)
            with self.assertRaises(zm.MCPError) as caught:
                await client.get_item('ABSENT01')
            self.assertEqual(caught.exception.status_code, 404)
            server.children = '# Child Items for: Only Notes\n\n## Notes\n - Key: NOTE0001'
            self.assertEqual(await client.get_attachments(PARENT), [])

    async def test_server_http_errors_redirects_and_rpc_failures_do_not_echo_credentials(self):
        for kind in ('redirect', 'auth', 'rpc', 'wrong_id', 'client_request', 'tool_error'):
            with self.subTest(kind=kind):
                server = Server()
                def override(request, payload):
                    if payload.get('method') != 'tools/list':
                        return None
                    if kind == 'redirect':
                        return httpx.Response(307, headers={'Location': 'http://169.254.169.254/secret'})
                    if kind == 'auth':
                        return httpx.Response(401, text='secret-credential')
                    if kind == 'rpc':
                        return httpx.Response(200, json={'jsonrpc': '2.0', 'id': payload['id'], 'error': {'message': 'secret-credential'}})
                    if kind == 'wrong_id':
                        return httpx.Response(200, json={'jsonrpc': '2.0', 'id': 'other', 'result': {}})
                    if kind == 'client_request':
                        return httpx.Response(200, json={'jsonrpc': '2.0', 'id': 'server-call', 'method': 'sampling/createMessage'})
                    return httpx.Response(200, json={'jsonrpc': '2.0', 'id': payload['id'], 'result': {'isError': True}})
                server.override = override
                async with server.client() as client:
                    with self.assertRaises(zm.MCPError) as caught:
                        await client.discover()
                    self.assertNotIn('secret-credential', caught.exception.message)
                self.assertTrue(all(request.url.host == '127.0.0.1' for request, _ in server.requests))

    async def test_session_expiration_reinitializes_once_without_replaying_mutations(self):
        server = Server()
        expired = []
        def override(request, payload):
            if payload.get('method') == 'tools/call' and not expired:
                expired.append(True)
                return httpx.Response(404)
        server.override = override
        async with server.client() as client:
            result = await client.get_item(PARENT)
            self.assertEqual(result['key'], PARENT)
        self.assertEqual(server.initializations, 2)
        for request, payload in server.requests:
            if payload.get('method') == 'initialize':
                self.assertNotIn('Mcp-Session-Id', request.headers)

    async def test_response_size_total_deadline_and_missing_sse_result(self):
        for kind in ('large', 'timeout', 'eof'):
            with self.subTest(kind=kind):
                server = Server()
                def override(request, payload):
                    if payload.get('method') != 'tools/list':
                        return None
                    if kind == 'large':
                        return httpx.Response(200, headers={'content-type': 'application/json'}, content=b'x' * 2049)
                    if kind == 'timeout':
                        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                                              stream=ChunkStream([b': keepalive\n\n'] * 20, delay=.01))
                    return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text=': only heartbeat\n\n')
                server.override = override
                with patch.object(zm, 'MAX_RESPONSE_BYTES', 2048), patch.object(zm, 'TOTAL_TIMEOUT', .05):
                    async with server.client() as client:
                        with self.assertRaises(zm.MCPError) as caught:
                            await client.discover()
                        self.assertEqual(caught.exception.status_code, 504 if kind == 'timeout' else 502)

    async def test_sse_resumption_uses_same_endpoint_and_event_id(self):
        server = Server()
        pending = []
        def override(request, payload):
            if payload.get('method') == 'tools/list' and not pending:
                pending.append(payload)
                return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text='id: continuation-1\nretry: 0\ndata: \n\n')
            if request.method == 'GET':
                self.assertEqual(request.headers['Last-Event-ID'], 'continuation-1')
                return server.result(request, pending[0], {'tools': server.tools})
        server.override = override
        async with server.client() as client:
            self.assertTrue((await client.discover())['capabilities']['metadata'])
        self.assertEqual(sum(request.method == 'GET' for request, _ in server.requests), 1)

    async def test_discovery_pagination_is_bounded_and_missing_tools_are_explicit(self):
        server = Server()
        def override(request, payload):
            if payload.get('method') == 'tools/list':
                cursor = payload.get('params', {}).get('cursor')
                tools = server.tools[:1] if not cursor else server.tools[1:]
                result = {'tools': tools}
                if not cursor:
                    result['nextCursor'] = 'next-page'
                return server.result(request, payload, result)
        server.override = override
        async with server.client() as client:
            self.assertEqual(len((await client.discover())['tools']), 3)
        server = Server(); server.tools = []
        async with server.client() as client:
            self.assertFalse((await client.discover())['capabilities']['search'])
            with self.assertRaises(zm.MCPError) as caught:
                await client.search('synthetic')
            self.assertEqual(caught.exception.status_code, 422)

    async def test_endpoint_normalization_dns_loopback_and_public_https_policy(self):
        self.assertEqual(zm.normalize_endpoint(' HTTP://LOCALHOST:8000/mcp '), 'http://localhost:8000/mcp')
        self.assertEqual(zm.normalize_endpoint('http://[::1]:8000/mcp'), 'http://[::1]:8000/mcp')
        for endpoint in ('stdio://server', 'file:///tmp/key', 'http://192.168.1.2/mcp', 'https://127.0.0.2/mcp',
                         'https://user:secret@example.org/mcp', 'https://example.org/mcp?token=secret',
                         'http://localhost.evil.example/mcp', 'http://127.1/mcp', 'https://example.org:99999',
                         'https://example.org/mcp#fragment', 'https://example.org/\r\nnext'):
            with self.subTest(endpoint=endpoint), self.assertRaises(zm.MCPError):
                zm.normalize_endpoint(endpoint)
        def resolved(address):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (address, 443))]
        with patch.object(zm.socket, 'getaddrinfo', return_value=resolved('127.0.0.1') + resolved('8.8.8.8')):
            with self.assertRaises(zm.MCPError):
                zm._validate_destination('http://localhost:8000/mcp')
        with patch.object(zm.socket, 'getaddrinfo', return_value=resolved('169.254.169.254')):
            with self.assertRaises(zm.MCPError):
                zm._validate_destination('https://mcp.example.org/mcp')
        with patch.object(zm.socket, 'getaddrinfo', return_value=resolved('8.8.8.8')):
            server = Server()
            async with server.client('https://mcp.example.org/mcp') as client:
                self.assertTrue((await client.discover())['capabilities']['search'])

    async def test_malformed_metadata_and_tool_resources_never_trigger_downloads(self):
        server = Server()
        async with server.client() as client:
            server.items[PARENT]['data']['key'] = 'OTHER001'
            with self.assertRaises(zm.MCPError):
                await client.get_item(PARENT)
            server.items[PARENT] = item(deleted=1)
            with self.assertRaises(zm.MCPError) as caught:
                await client.get_item(PARENT)
            self.assertEqual(caught.exception.status_code, 404)
            def override(request, payload):
                if payload.get('method') == 'tools/call':
                    return server.result(request, payload, {'content': [{'type': 'resource_link', 'uri': 'file:///private/secret'}]})
            server.override = override
            with self.assertRaises(zm.MCPError):
                await client.get_item(PARENT)
        self.assertTrue(all(request.url.path == '/mcp' for request, _ in server.requests))

    async def test_old_metadata_schema_is_reported_unavailable_before_tool_call(self):
        server = Server()
        for tool in server.tools:
            if tool['name'] == 'zotero_get_item_metadata':
                tool['inputSchema']['properties'] = {'format': {'enum': ['markdown', 'bibtex']}}
        async with server.client() as client:
            capabilities = (await client.discover())['capabilities']
            self.assertFalse(capabilities['metadata'] or capabilities['attachments'])
            with self.assertRaises(zm.MCPError) as caught:
                await client.get_item(PARENT)
            self.assertEqual(caught.exception.status_code, 422)
        self.assertFalse(any(payload.get('method') == 'tools/call' for _, payload in server.requests))

    async def test_attachment_bad_basename_invalid_key_and_bearer_header_are_not_accepted(self):
        for token in ('bad\r\nAuthorization: x', '包含非ASCII', 'space inside'):
            with self.assertRaises(zm.MCPError):
                zm.ZoteroMCPClient('http://127.0.0.1:8000/mcp', token)
        server = Server()
        server.items[ATTACHMENT]['data']['filename'] = '/private/path/secret.pdf'
        async with server.client() as client:
            attachments = await client.get_attachments(PARENT)
            self.assertEqual(attachments[0]['filename'], '')
            self.assertNotIn('/private/path', json.dumps(attachments))
            server.children = CHILDREN.replace('   - Key: ATTACH01', '   - Key: ../invalid')
            with self.assertRaises(zm.MCPError):
                await client.get_attachments(PARENT)


if __name__ == '__main__':
    unittest.main()
