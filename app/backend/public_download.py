"""Fetch bounded public web resources, connecting only to validated numeric addresses."""
import asyncio
import ipaddress
import re
import socket
import zlib
from urllib.parse import urljoin

import httpx

from external import USER_AGENT


class ArticleLinkError(ValueError):
    pass


HTML_LIMIT = 2 * 1024 * 1024
PDF_LIMIT = 80 * 1024 * 1024
REDIRECTS = {301, 302, 303, 307, 308}
TRANSITION_NETS = tuple(ipaddress.ip_network(net) for net in
                        ('64:ff9b::/96', '64:ff9b:1::/48', '2001::/32', '2002::/16'))


def _public_ip(ip):
    if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return False
    return not isinstance(ip, ipaddress.IPv6Address) or (
        not ip.ipv4_mapped and not any(ip in block for block in TRANSITION_NETS))


def public_url(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 4096 or re.search(r'[\x00-\x20\x7f\\]', value):
        raise ArticleLinkError('请输入有效的公开 HTTP 或 HTTPS 地址。')
    try:
        url = httpx.URL(value)
        host = url.raw_host.decode().lower().rstrip('.')
        if (url.scheme not in ('http', 'https') or not host or url.userinfo
                or '@' in value.partition('://')[2].split('/')[0].split('?')[0].split('#')[0]
                or url.port not in (None, 80 if url.scheme == 'http' else 443) or '%' in host):
            raise ValueError()
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            if ('.' not in host or not re.fullmatch(r'[a-z0-9.-]+', host) or
                    host.endswith(('.localhost', '.local', '.internal', '.home.arpa', '.onion'))):
                raise ValueError()
        else:
            if not _public_ip(ip):
                raise ValueError()
        return str(url.copy_with(host=host, fragment=None))
    except (ValueError, UnicodeError, httpx.InvalidURL) as exc:
        raise ArticleLinkError('该链接不是可访问的公开 HTTP 或 HTTPS 地址。') from exc


async def _addresses(url):
    host = url.raw_host.decode()
    port = url.port or (443 if url.scheme == 'https' else 80)
    try:
        records = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addrs = list(dict.fromkeys(ipaddress.ip_address(record[4][0]) for record in records))
    except (OSError, ValueError) as exc:
        raise ArticleLinkError('无法解析链接的主机地址。') from exc
    if not addrs or any(not _public_ip(ip) for ip in addrs):
        raise ArticleLinkError('链接解析到非公开网络地址。')
    return addrs


async def _request(client, url):
    addrs = await _addresses(url)
    host = url.raw_host.decode()
    authority = '[' + host + ']' if ':' in host else host
    headers = {'Host': authority, 'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity',
               'Accept': 'application/pdf, text/html, application/xhtml+xml, */*;q=0.2', 'Connection': 'close'}
    for ip in addrs[:4]:
        # HTTP authority and TLS certificate validation still use the original host.
        # Each connection is made to a numeric address, without a second lookup of that host.
        request = httpx.Request('GET', url.copy_with(host=str(ip)), headers=headers,
                                extensions={'sni_hostname': host})
        try:
            return await client.send(request, stream=True, follow_redirects=False)
        except (httpx.ConnectTimeout, httpx.ConnectError):
            continue
    raise ArticleLinkError('无法连接到链接的公开主机。')


async def _body(response, limit):
    mime = response.headers.get('content-type', '').split(';')[0].lower().strip()
    length = response.headers.get('content-length', '')
    is_html = mime in ('text/html', 'application/xhtml+xml') or mime.startswith('text/')
    max_size = min(limit, HTML_LIMIT) if is_html else limit
    if length.isdigit() and int(length) > max_size:
        raise ArticleLinkError('链接返回的文件超过大小限制。')
    encoding = response.headers.get('content-encoding', 'identity').strip().lower()
    if encoding not in ('', 'identity', 'gzip'):
        raise ArticleLinkError('链接返回了不支持的传输编码。')
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == 'gzip' else None
    data = bytearray()
    transferred = 0
    async for chunk in response.aiter_raw(chunk_size=65536):
        transferred += len(chunk)
        if transferred > max_size:
            raise ArticleLinkError('链接返回的文件超过大小限制。')
        if inflater:
            chunk = inflater.decompress(chunk, max_size - len(data) + 1)
        data.extend(chunk)
        # Catch mislabeled HTML without buffering a large non-PDF body.
        if len(data) >= 5 and not data.startswith(b'%PDF-'):
            max_size = min(limit, HTML_LIMIT)
        if len(data) > max_size:
            raise ArticleLinkError('链接返回的文件超过大小限制。')
    if inflater and (not inflater.eof or inflater.unused_data):
        raise ArticleLinkError('链接返回了无效的压缩文件。')
    return bytes(data)


async def _fetch(url, limit):
    # Disable proxies, ambient cookies and keepalive across different hostnames on the same IP.
    limits = httpx.Limits(max_keepalive_connections=0)
    async with httpx.AsyncClient(trust_env=False, limits=limits, timeout=httpx.Timeout(12, connect=5)) as client:
        for _ in range(6):
            url = public_url(url)
            response = await _request(client, httpx.URL(url))
            try:
                if response.status_code in REDIRECTS:
                    location = response.headers.get('location')
                    if not location:
                        raise ArticleLinkError('重定向未提供目标地址。')
                    url = urljoin(url, location)
                    continue
                if response.status_code != 200:
                    raise ArticleLinkError(f'链接返回 HTTP {response.status_code}。')
                return url, response.headers.get('content-type', ''), await _body(response, limit)
            finally:
                await response.aclose()
    raise ArticleLinkError('链接的重定向次数过多。')


async def fetch_public(url, limit=PDF_LIMIT):
    try:
        return await asyncio.wait_for(_fetch(url, min(PDF_LIMIT, limit)), timeout=45)
    except asyncio.TimeoutError as exc:
        raise ArticleLinkError('查找链接超时，请稍后重试。') from exc
    except (httpx.HTTPError, httpx.InvalidURL, OSError, zlib.error) as exc:
        raise ArticleLinkError('无法读取链接的内容。') from exc
