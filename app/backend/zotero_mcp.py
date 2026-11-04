"""Read-only Zotero MCP adapter over bounded Streamable HTTP.

No subprocesses, roots, sampling, filesystem reads or attachment URL downloads.
The adapter recognizes the explicit 54yyyu/zotero-mcp metadata/search contracts.
"""
import asyncio
import codecs
import ipaddress
import json
import re
import socket
import time
import uuid
from urllib.parse import urlsplit, urlunsplit

import httpx

PROTOCOL_VERSIONS = ('2025-11-25', '2025-06-18')
TOTAL_TIMEOUT = 60.0
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_SEARCH_WINDOW = 100
MAX_ATTACHMENTS = 40
READ_TOOLS = frozenset(('zotero_search_items', 'zotero_get_item_metadata', 'zotero_get_item_children'))
LOOPBACK_HOSTS = frozenset(('localhost', '127.0.0.1', '::1'))
KEY = re.compile(r'[A-Z0-9]{8}')


class MCPError(RuntimeError):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def normalize_endpoint(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise MCPError(400, '请填写有效的 Zotero MCP HTTP 地址')
    value = value.strip()
    if re.search(r'[\x00-\x20\x7f\\]', value):
        raise MCPError(400, 'MCP 地址不能包含空格、控制字符或反斜杠')
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
        if not host or parsed.username is not None or parsed.password is not None or parsed.fragment or parsed.query:
            raise ValueError()
        host = host.lower().encode('idna').decode('ascii')
        if '%' in host or parsed.scheme not in ('http', 'https') or port is not None and not 1 <= port <= 65535:
            raise ValueError()
        if parsed.scheme == 'http' and host not in LOOPBACK_HOSTS:
            raise MCPError(400, 'HTTP MCP 地址仅允许 localhost、127.0.0.1 或 ::1；远程服务请使用 HTTPS')
        if host not in LOOPBACK_HOSTS:
            if host.endswith('.localhost') or host.endswith('.local'):
                raise ValueError()
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if address is not None and not _public_ip(address):
                raise ValueError()
        authority = f'[{host}]' if ':' in host else host
        if port is not None and port != (443 if parsed.scheme == 'https' else 80):
            authority += ':' + str(port)
        return urlunsplit((parsed.scheme, authority, parsed.path or '/', '', ''))
    except (ValueError, UnicodeError) as exc:
        raise MCPError(400, 'MCP 地址无效：仅支持本机 HTTP 或公网 HTTPS，不能内嵌账号、查询参数或片段') from exc


def _public_ip(address):
    return (address.is_global and not address.is_multicast and not address.is_reserved and
            not address.is_private and not address.is_loopback and not address.is_link_local and
            not address.is_unspecified)


def _validate_destination(endpoint):
    parsed = urlsplit(endpoint)
    host = parsed.hostname
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == 'https' else 80),
                                   type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise MCPError(502, '无法解析 Zotero MCP 地址，请检查服务是否启动') from exc
    if not infos:
        raise MCPError(502, 'Zotero MCP 地址没有可用的网络地址')
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError as exc:
            raise MCPError(400, 'MCP 地址解析结果无效') from exc
        allowed = address.is_loopback if host in LOOPBACK_HOSTS else _public_ip(address)
        if not allowed:
            raise MCPError(400, 'MCP 地址解析到不允许的网络位置，请检查配置')


def _key(value):
    if not isinstance(value, str) or not KEY.fullmatch(value):
        raise MCPError(400, 'Zotero 条目键必须是 8 位大写字母或数字')
    return value


def _text(value, limit=2000):
    if not isinstance(value, str):
        return ''
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', value).strip()[:limit]


def _year(value):
    match = re.search(r'\b(?:1[5-9]\d{2}|20\d{2}|2100)\b', _text(value, 100))
    return match[0] if match else ''


def _display_url(value):
    value = _text(value)
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            return ''
        if re.search(r'[\x00-\x20\x7f]', value):
            return ''
        return value
    except ValueError:
        return ''


def _filename(value):
    # A basename is metadata; a returned filesystem path is never exposed or used.
    if (not isinstance(value, str) or len(value) > 1000 or value in ('.', '..') or
            re.search(r'[/\\\x00-\x1f\x7f]', value)):
        return ''
    return value


def normalize_item(raw, expected_key):
    if not isinstance(raw, dict) or not isinstance(raw.get('data'), dict):
        raise MCPError(502, 'Zotero 返回的条目元数据格式不兼容')
    data = raw['data']
    returned_key = raw.get('key') or data.get('key')
    if returned_key != expected_key or data.get('key', expected_key) != expected_key:
        raise MCPError(502, 'Zotero 返回的条目键与请求不一致')
    if data.get('deleted'):
        raise MCPError(404, '该 Zotero 条目已在回收站中，请先在 Zotero 恢复')
    library = raw.get('library') if isinstance(raw.get('library'), dict) else {}
    library_type = library.get('type') if library.get('type') in ('user', 'group') else ''
    library_id = library.get('id')
    if isinstance(library_id, str) and re.fullmatch(r'\d{1,18}', library_id):
        library_id = int(library_id)
    if type(library_id) is not int or library_id < 0 or library_id > 10 ** 18 or not library_type:
        library_id = None
    creators = data.get('creators') if isinstance(data.get('creators'), list) else []
    names = []
    for creator in creators[:50]:
        if isinstance(creator, dict):
            name = _text(creator.get('name'), 200) or ' '.join(filter(None, (
                _text(creator.get('firstName'), 100), _text(creator.get('lastName'), 100))))
            if name:
                names.append(name)
    tags = []
    for tag in data.get('tags', []) if isinstance(data.get('tags'), list) else []:
        value = _text(tag.get('tag'), 40) if isinstance(tag, dict) else ''
        if value and value.casefold() not in {item.casefold() for item in tags}:
            tags.append(value)
        if len(tags) >= 30:
            break
    doi = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', _text(data.get('DOI'), 300), flags=re.I)
    if not re.fullmatch(r'10\.\d{4,9}/\S+', doi):
        doi = ''
    return {'key': expected_key, 'item_key': expected_key, 'library_type': library_type, 'library_id': library_id,
            'item_type': _text(data.get('itemType'), 80), 'title': _text(data.get('title'), 2000),
            'authors': '; '.join(names), 'year': _year(data.get('date')), 'doi': doi,
            'journal': _text(data.get('publicationTitle') or data.get('proceedingsTitle'), 500),
            'abstract': _text(data.get('abstractNote'), 30000), 'url': _display_url(data.get('url')), 'tags': tags,
            'parent_key': _text(data.get('parentItem'), 100), 'filename': _filename(data.get('filename')),
            'content_type': _text(data.get('contentType'), 100), 'link_mode': _text(data.get('linkMode'), 100)}


class ZoteroMCPClient:
    def __init__(self, endpoint, bearer='', transport=None):
        self.endpoint = normalize_endpoint(endpoint)
        if not isinstance(bearer, str) or len(bearer) > 8192 or not re.fullmatch(r'[\x21-\x7e]*', bearer.strip()):
            raise MCPError(400, 'MCP Bearer Token 格式无效')
        self._bearer = bearer.strip()
        self._transport = transport
        self._client = None
        self._session = None
        self._version = None
        self._tools = None
        self._server = {}
        self._deadline = 0

    async def __aenter__(self):
        self._deadline = time.monotonic() + TOTAL_TIMEOUT
        self._client = httpx.AsyncClient(transport=self._transport, follow_redirects=False, trust_env=False)
        try:
            await self._initialize()
            return self
        except BaseException:
            await self._client.aclose()
            self._client = None
            raise

    async def __aexit__(self, exc_type, exc, tb):
        if self._client is not None:
            try:
                if self._session and time.monotonic() < self._deadline:
                    async with asyncio.timeout(min(1, self._remaining())):
                        await asyncio.to_thread(_validate_destination, self.endpoint)
                        async with self._client.stream('DELETE', self.endpoint, headers=self._headers(), timeout=1):
                            pass
            except Exception:
                pass
            finally:
                await self._client.aclose()
                self._client = None

    def _remaining(self):
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise MCPError(504, 'Zotero MCP 操作超时，请稍后重试')
        return remaining

    def _headers(self):
        headers = {'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json'}
        if self._bearer:
            headers['Authorization'] = 'Bearer ' + self._bearer
        if self._session:
            headers['Mcp-Session-Id'] = self._session
        if self._version:
            headers['MCP-Protocol-Version'] = self._version
        return headers

    @staticmethod
    def _event(raw, request_id):
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise MCPError(502, 'MCP 返回了无法解析的数据') from exc
        if not isinstance(event, dict) or event.get('jsonrpc') != '2.0':
            raise MCPError(502, 'MCP JSON-RPC 响应格式不兼容')
        if 'method' in event:
            if 'id' in event:
                raise MCPError(502, 'MCP 请求了 BioReader 未启用的客户端能力')
            return None
        if event.get('id') != request_id:
            raise MCPError(502, 'MCP 响应与当前请求不匹配')
        if 'error' in event:
            raise MCPError(502, 'Zotero MCP 返回协议错误，请检查服务版本与配置')
        if not isinstance(event.get('result'), dict):
            raise MCPError(502, 'MCP 响应缺少有效结果')
        return event['result']

    async def _consume(self, response, request_id, budget):
        content_type = response.headers.get('content-type', '').split(';')[0].lower().strip()
        if content_type not in ('application/json', 'text/event-stream'):
            raise MCPError(502, 'MCP 服务必须返回 JSON 或 Streamable HTTP 事件流')
        if response.headers.get('content-length', '').isdigit() and int(response.headers['content-length']) > budget:
            raise MCPError(502, 'MCP 返回数据超过允许大小，请缩小查询范围')
        total, buffer, data, last_id, retry_ms = 0, '', [], None, 1000
        decoder = codecs.getincrementaldecoder('utf-8')()
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > budget:
                raise MCPError(502, 'MCP 返回数据超过允许大小，请缩小查询范围')
            try:
                buffer += decoder.decode(chunk)
            except UnicodeError as exc:
                raise MCPError(502, 'MCP 返回的文本编码无效') from exc
            if content_type == 'application/json':
                continue
            while True:
                end = re.search(r'\r\n|\r|\n', buffer)
                if end is None or end[0] == '\r' and end.end() == len(buffer):
                    break
                line, buffer = buffer[:end.start()], buffer[end.end():]
                if line.startswith('data:'):
                    data.append(line[5:].removeprefix(' '))
                elif line.startswith('id:'):
                    value = line[3:].strip()
                    if value and len(value) <= 512 and not re.search(r'[\x00-\x1f\x7f]', value):
                        last_id = value
                elif line.startswith('retry:') and line[6:].strip().isdigit():
                    retry_ms = int(line[6:].strip())
                elif not line:
                    if data and ''.join(data).strip():
                        result = self._event('\n'.join(data), request_id)
                        if result is not None:
                            return result, last_id, retry_ms, total
                    data = []
        try:
            buffer += decoder.decode(b'', final=True)
        except UnicodeError as exc:
            raise MCPError(502, 'MCP 返回的文本编码不完整') from exc
        if content_type == 'application/json':
            result = self._event(buffer, request_id)
            if result is None:
                raise MCPError(502, 'MCP 没有返回当前请求结果')
            return result, None, retry_ms, total
        if buffer.startswith('data:'):
            data.append(buffer[5:].removeprefix(' ').rstrip('\r'))
        if data and ''.join(data).strip():
            result = self._event('\n'.join(data), request_id)
            if result is not None:
                return result, last_id, retry_ms, total
        return None, last_id, retry_ms, total

    async def _exchange(self, payload):
        if self._client is None:
            raise MCPError(500, 'MCP 连接尚未初始化')
        try:
            async with asyncio.timeout(self._remaining()):
                method, headers, budget = 'POST', self._headers(), MAX_RESPONSE_BYTES
                for attempt in range(4):
                    await asyncio.to_thread(_validate_destination, self.endpoint)
                    async with self._client.stream(method, self.endpoint, headers=headers,
                                                   json=payload if method == 'POST' else None,
                                                   timeout=httpx.Timeout(self._remaining(), connect=min(10, self._remaining()))) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            raise MCPError(400, 'MCP 地址发生重定向，请填写服务的最终地址')
                        if response.status_code >= 400:
                            status = response.status_code
                            if status == 404 and self._session:
                                raise MCPError(409, 'MCP 会话已失效，请重试连接')
                            if status in (401, 403):
                                raise MCPError(502, f'MCP 认证失败（HTTP {status}），请检查 Bearer Token')
                            raise MCPError(502, f'MCP 连接失败（HTTP {status}），请检查地址与服务状态')
                        session = response.headers.get('Mcp-Session-Id')
                        if payload.get('method') == 'initialize' and session:
                            if len(session) > 512 or not re.fullmatch(r'[\x21-\x7e]+', session):
                                raise MCPError(502, 'MCP 返回了无效的会话标识')
                            self._session = session
                        if 'id' not in payload:
                            if response.status_code not in (200, 202, 204):
                                raise MCPError(502, 'MCP 未接受客户端初始化通知')
                            return {}
                        result, last_id, retry_ms, used = await self._consume(response, payload['id'], budget)
                        budget -= used
                        if result is not None:
                            return result
                    if last_id is None or attempt == 3:
                        raise MCPError(502, 'MCP 事件流提前结束，未取得完整结果')
                    await asyncio.sleep(retry_ms / 1000)
                    method = 'GET'
                    headers['Last-Event-ID'] = last_id
                raise MCPError(502, 'MCP 事件流未返回完整结果')
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise MCPError(504, 'Zotero MCP 操作超时，请稍后重试') from exc
        except httpx.HTTPError as exc:
            raise MCPError(502, '无法连接 Zotero MCP，请检查服务是否启动及地址是否正确') from exc

    async def _rpc(self, method, params=None, retry_session=True):
        payload = {'jsonrpc': '2.0', 'id': uuid.uuid4().hex, 'method': method}
        if params is not None:
            payload['params'] = params
        try:
            return await self._exchange(payload)
        except MCPError as exc:
            if exc.status_code != 409 or not retry_session:
                raise
            self._session = None
            self._version = None
            self._tools = None
            await self._initialize()
            return await self._exchange(payload)

    async def _initialize(self):
        result = await self._rpc('initialize', {'protocolVersion': PROTOCOL_VERSIONS[0], 'capabilities': {},
                                              'clientInfo': {'name': 'BioReader', 'version': '1.0'}}, retry_session=False)
        version = result.get('protocolVersion')
        if version not in PROTOCOL_VERSIONS:
            raise MCPError(502, 'MCP 协议版本不兼容，需要 Streamable HTTP 2025-06-18 或 2025-11-25')
        if not isinstance(result.get('capabilities'), dict):
            raise MCPError(502, 'MCP 初始化缺少有效的服务能力信息')
        self._version = version
        info = result.get('serverInfo') if isinstance(result.get('serverInfo'), dict) else {}
        self._server = {'name': _text(info.get('name'), 120), 'version': _text(info.get('version'), 80)}
        self._server_tools = 'tools' in result['capabilities']
        await self._exchange({'jsonrpc': '2.0', 'method': 'notifications/initialized'})

    async def discover(self):
        if self._tools is None:
            found, cursor, seen = {}, None, set()
            if self._server_tools:
                for _ in range(10):
                    result = await self._rpc('tools/list', {'cursor': cursor} if cursor else {})
                    entries = result.get('tools')
                    if not isinstance(entries, list) or len(entries) > 500:
                        raise MCPError(502, 'MCP 工具列表格式不兼容或过大')
                    for tool in entries:
                        if not isinstance(tool, dict) or not isinstance(tool.get('name'), str):
                            raise MCPError(502, 'MCP 工具描述无效')
                        name = tool['name']
                        if name in found:
                            raise MCPError(502, 'MCP 返回了重复的工具名称')
                        found[name] = tool
                    if len(found) > 500:
                        raise MCPError(502, 'MCP 工具列表超过允许数量')
                    cursor = result.get('nextCursor')
                    if not cursor:
                        break
                    if not isinstance(cursor, str) or len(cursor) > 2000 or cursor in seen:
                        raise MCPError(502, 'MCP 工具分页标识无效')
                    seen.add(cursor)
                else:
                    raise MCPError(502, 'MCP 工具分页超过允许范围')
            self._tools = {name: tool for name, tool in found.items() if name in READ_TOOLS}
        metadata_tool = self._tools.get('zotero_get_item_metadata', {})
        schema = metadata_tool.get('inputSchema')
        properties = schema.get('properties', {}) if isinstance(schema, dict) else {}
        output_format = properties.get('format', {}) if isinstance(properties, dict) else {}
        enum = output_format.get('enum') if isinstance(output_format, dict) else None
        metadata_json = 'zotero_get_item_metadata' in self._tools and (not isinstance(enum, list) or 'json' in enum)
        capabilities = {'search': 'zotero_search_items' in self._tools,
                        'metadata': metadata_json,
                        'attachments': metadata_json and 'zotero_get_item_children' in self._tools}
        return {'server_name': self._server['name'], 'server': dict(self._server), 'protocol_version': self._version,
                'tools': sorted(self._tools), 'capabilities': capabilities,
                'message': '' if metadata_json else '当前服务缺少 JSON 元数据能力，请更新兼容的 Zotero MCP'}

    async def _call_tool(self, name, arguments):
        if name not in READ_TOOLS:
            raise MCPError(400, 'BioReader 未启用该 MCP 工具')
        discovered = await self.discover()
        if name not in self._tools:
            raise MCPError(422, '当前 MCP 服务缺少所需的 Zotero 只读工具')
        if name == 'zotero_get_item_metadata' and not discovered['capabilities']['metadata']:
            raise MCPError(422, '当前 MCP 服务不支持 JSON 元数据，请更新兼容的 Zotero MCP')
        result = await self._rpc('tools/call', {'name': name, 'arguments': arguments})
        if result.get('isError'):
            raise MCPError(502, 'Zotero MCP 工具执行失败，请检查服务配置')
        structured = result.get('structuredContent')
        if isinstance(structured, dict):
            if isinstance(structured.get('data'), dict):
                return structured
            if isinstance(structured.get('result'), str):
                return structured['result']
        blocks = result.get('content')
        if not isinstance(blocks, list):
            raise MCPError(502, 'Zotero MCP 工具没有返回可读取的结果')
        texts = [block['text'] for block in blocks if isinstance(block, dict) and block.get('type') == 'text' and isinstance(block.get('text'), str)]
        if not texts:
            raise MCPError(502, 'Zotero MCP 工具没有返回文本元数据')
        text = '\n'.join(texts)
        if re.match(r'^\s*(?:Error\b|Failed\b)', text, re.I):
            raise MCPError(502, 'Zotero MCP 未能读取文库，请检查服务连接与权限')
        return text

    async def search(self, query, limit=20, start=0):
        if not isinstance(query, str) or not query.strip() or len(query) > 300:
            raise MCPError(400, '请输入 1–300 字符的检索词')
        if type(limit) is not int or not 1 <= limit <= 50 or type(start) is not int or not 0 <= start < MAX_SEARCH_WINDOW:
            raise MCPError(400, '检索分页范围无效')
        end = min(start + limit, MAX_SEARCH_WINDOW)
        count = min(end + 1, MAX_SEARCH_WINDOW)
        text = await self._call_tool('zotero_search_items', {'query': query.strip(), 'limit': count,
                                                           'qmode': 'titleCreatorYear', 'item_type': '-attachment',
                                                           'search_all_libraries': False})
        if not isinstance(text, str):
            raise MCPError(502, 'Zotero 搜索结果格式不兼容')
        sections = re.split(r'^##\s+\d+\.\s+', text, flags=re.M)[1:]
        if not sections:
            if re.search(r'\bNo (?:items|results|matching items) found\b', text, re.I):
                return {'items': [], 'has_more': False}
            raise MCPError(502, 'Zotero 搜索结果格式不兼容，不能可靠读取条目')
        items, seen = [], set()
        for index, section in enumerate(sections):
            def field(label):
                match = re.search(r'^\*\*' + label + r':\*\*\s*(.*)$', section, re.M)
                return match[1].strip() if match else ''
            key = field('Item Key').strip('`')
            if not KEY.fullmatch(key) or key in seen:
                raise MCPError(502, 'Zotero 搜索结果包含无效或重复条目键')
            seen.add(key)
            kind = field('Type')
            if kind in ('attachment', 'note', 'annotation') or not start <= index < end:
                continue
            items.append({'key': key, 'title': _text(section.splitlines()[0]), 'authors': _text(field('Authors')),
                          'year': _year(field('Date')), 'doi': '', 'item_type': _text(kind, 80)})
        return {'items': items, 'has_more': end < MAX_SEARCH_WINDOW and len(sections) > end}

    async def get_item(self, key):
        key = _key(key)
        result = await self._call_tool('zotero_get_item_metadata', {'item_key': key, 'format': 'json'})
        if isinstance(result, str):
            if result.startswith('No item found with key:'):
                raise MCPError(404, '未找到该 Zotero 条目')
            try:
                result = json.loads(result)
            except ValueError as exc:
                raise MCPError(502, 'Zotero 元数据不是有效 JSON，请更新兼容的 MCP 服务') from exc
        return normalize_item(result, key)

    async def get_attachments(self, parentkey, parent=None):
        parentkey = _key(parentkey)
        if parent is None:
            parent = await self.get_item(parentkey)
        if not isinstance(parent, dict) or parent.get('key') != parentkey:
            raise MCPError(400, '附件所属文献与请求不一致')
        result = await self._call_tool('zotero_get_item_children', {'item_key': parentkey})
        if not isinstance(result, str):
            raise MCPError(502, 'Zotero 附件列表格式不兼容')
        match = re.search(r'^## Attachments\s*\n(.*?)(?=^## |\Z)', result, re.M | re.S)
        if match is None:
            if re.search(r'\bNo (?:child items|children|attachments) found\b', result, re.I) or result.startswith('# Child Items for:'):
                return []
            raise MCPError(502, 'Zotero 附件列表无法可靠解析')
        keys = [value.strip('`') for value in re.findall(r'^\s+- Key:[ \t]*(.*?)[ \t]*$', match[1], re.M)]
        if any(not KEY.fullmatch(key) for key in keys) or len(keys) != len(set(keys)) or len(keys) > MAX_ATTACHMENTS:
            raise MCPError(502, 'Zotero 附件数量过多或条目键重复，请在 Zotero 整理后重试')
        if match[1].strip() and not keys:
            raise MCPError(502, 'Zotero 附件列表不包含有效条目键')
        attachments = []
        for key in keys:
            item = await self.get_item(key)
            if item['item_type'] != 'attachment' or item['parent_key'] != parentkey:
                raise MCPError(502, 'Zotero 附件的所属文献发生变化，请刷新重试')
            parent_identity = (parent.get('library_type'), parent.get('library_id'))
            item_identity = (item['library_type'], item['library_id'])
            if all(value is not None and value != '' for value in parent_identity + item_identity) and parent_identity != item_identity:
                raise MCPError(502, 'Zotero 附件与文献不属于同一文库，请刷新重试')
            attachments.append({name: item[name] for name in ('key', 'parent_key', 'title', 'filename', 'content_type',
                                                              'link_mode', 'library_type', 'library_id')})
        return attachments
