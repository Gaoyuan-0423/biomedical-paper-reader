"""Explicit local Zotero configuration, short-lived previews and managed PDFs."""
import copy
import hashlib
import json
import os
import re
import stat
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException
from pydantic import BaseModel, Field

from reading_workflow import atomic_json
from zotero_mcp import MCPError, normalize_endpoint

KEY = re.compile(r'[A-Z0-9]{8}')
MAX_PDF = 80 * 1024 * 1024
_previews = {}
_preview_lock = threading.RLock()


class ZoteroConfigIn(BaseModel):
    endpoint: str | None = Field(default=None, max_length=2048)
    storage_root: str | None = Field(default=None, max_length=4096)
    token: str | None = Field(default=None, max_length=4096)
    clear_token: bool = False


class ZoteroSearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=20, ge=1, le=20)
    start: int = Field(default=0, ge=0, le=99)


class ZoteroImportIn(BaseModel):
    preview_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    attachment_key: str = Field(default='', pattern=r'^(?:[A-Z0-9]{8})?$')


def read_config(directory):
    path = Path(directory) / 'zotero.json'
    if not path.exists():
        return {'endpoint': 'http://127.0.0.1:8000/mcp', 'token': '',
                'storage_root': str(Path.home() / 'Zotero' / 'storage'), 'configuration_id': 'unconfigured'}
    try:
        if path.stat().st_size > 16384:
            raise ValueError()
        result = json.loads(path.read_text('utf-8'))
        if not isinstance(result, dict) or any(not isinstance(result.get(k), str) for k in ('endpoint', 'token', 'storage_root', 'configuration_id')):
            raise ValueError()
        normalize_endpoint(result['endpoint'])
        return result
    except (OSError, ValueError, TypeError, MCPError) as exc:
        raise HTTPException(409, 'Zotero 连接设置无法读取，原文件已保留，请检查 zotero.json 后重试') from exc


def public_config(config):
    return {key: config[key] for key in ('endpoint', 'storage_root', 'configuration_id')} | {'has_token': bool(config['token'])}


def merge_config(current, body):
    updated = dict(current)
    if body.endpoint is not None:
        try:
            updated['endpoint'] = normalize_endpoint(body.endpoint.strip())
        except MCPError as exc:
            raise HTTPException(exc.status_code, exc.message) from exc
        if updated['endpoint'] != current['endpoint']:
            updated['token'] = ''  # Credentials are never forwarded to a newly entered server.
    if body.storage_root is not None:
        raw = body.storage_root.strip()
        if '\x00' in raw or '\n' in raw or '\r' in raw:
            raise HTTPException(422, '附件目录格式不正确')
        path = Path(raw).expanduser() if raw else None
        if path is not None and not path.is_absolute():
            raise HTTPException(422, '请填写 Zotero 附件目录的绝对路径或 ~/ 开头的路径')
        updated['storage_root'] = str(path) if path else ''
    if body.clear_token:
        updated['token'] = ''
    elif body.token and body.token.strip():
        value = body.token.strip()
        if any(not 33 <= ord(char) <= 126 for char in value):
            raise HTTPException(422, '连接令牌格式不正确')
        updated['token'] = value
    if any(updated[k] != current[k] for k in ('endpoint', 'storage_root', 'token')) or current['configuration_id'] == 'unconfigured':
        updated['configuration_id'] = uuid.uuid4().hex
    return updated


def write_config(directory, config):
    try:
        Path(directory).mkdir(parents=True, exist_ok=True)
        atomic_json(Path(directory) / 'zotero.json', config)
    except OSError as exc:
        raise HTTPException(503, 'Zotero 设置保存失败，已有设置保留，请检查磁盘后重试') from exc


def same_config(directory, config):
    if read_config(directory)['configuration_id'] != config['configuration_id']:
        raise HTTPException(409, 'Zotero 连接设置已更改，请重新搜索或选择文献')


def source_identity(config, item, attachment_key=''):
    return {'server_id': hashlib.sha256(config['endpoint'].encode()).hexdigest(),
            'library_type': item.get('library_type', ''), 'library_id': item.get('library_id'),
            'item_key': item['key'], 'attachment_key': attachment_key}


def _doi(value):
    value = str(value or '').strip().lower()
    return re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', value).rstrip('.')


def duplicate(papers, config, item):
    identity = source_identity(config, item)
    for paper in papers:
        old = paper.get('zotero_source') or {}
        if all(old.get(k) == identity[k] for k in ('server_id', 'library_type', 'library_id', 'item_key')):
            return paper
        if _doi(item.get('doi')) and _doi(paper.get('doi')) == _doi(item['doi']):
            return paper
    return None


def _managed_path(config, parent, attachment):
    if urlparse(config['endpoint']).hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise HTTPException(422, '远程 MCP 的附件不能作为本机文件读取；可仅导入文献信息')
    if not config['storage_root']:
        raise HTTPException(422, '请先设置本机 Zotero 附件目录')
    if not parent.get('library_type') or parent.get('library_id') is None:
        raise HTTPException(422, '无法确认附件所属文献库，可仅导入文献信息')
    if (attachment.get('parent_key') != parent['key'] or
            attachment.get('library_type') != parent['library_type'] or
            str(attachment.get('library_id')) != str(parent['library_id'])):
        raise HTTPException(422, '附件不属于所选文献及文献库')
    if attachment.get('link_mode') not in ('imported_file', 'imported_url'):
        raise HTTPException(422, '此附件是链接文件，请先在 Zotero 保存本地副本，或手动导入 PDF')
    key, filename = attachment.get('key', ''), attachment.get('filename', '')
    if (not KEY.fullmatch(key) or not isinstance(filename, str) or not filename or
            any(c in filename for c in ('/', '\\', '\x00')) or filename in ('.', '..') or
            not filename.lower().endswith('.pdf') or attachment.get('content_type') != 'application/pdf'):
        raise HTTPException(422, '附件不是可导入的 PDF')
    root = Path(config['storage_root']).expanduser().resolve()
    candidate = root / key / filename
    try:
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root) or candidate.is_symlink() or (root / key).is_symlink():
            raise HTTPException(422, '附件路径超出配置的 Zotero 目录')
        info = resolved.stat()
        if not stat.S_ISREG(info.st_mode):
            raise HTTPException(422, '附件不是普通 PDF 文件')
        if info.st_size > MAX_PDF:
            raise HTTPException(413, 'PDF 超过 80 MB 限制')
        return resolved
    except OSError as exc:
        raise HTTPException(422, '本机未找到该 PDF，请先在 Zotero 下载附件，并检查附件目录') from exc


def attachment_view(config, parent, attachment):
    view = {key: attachment.get(key, '') for key in ('key', 'title', 'filename', 'content_type')}
    try:
        _managed_path(config, parent, attachment)
        view.update(available=True, unavailable_reason='')
    except HTTPException as exc:
        view.update(available=False, unavailable_reason=str(exc.detail))
    return view


def read_managed_pdf(config, parent, attachment):
    path = _managed_path(config, parent, attachment)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise HTTPException(422, '附件不是普通 PDF 文件')
            content = stream.read(MAX_PDF + 1)
    except OSError as exc:
        raise HTTPException(422, '无法读取所选 PDF，请在 Zotero 下载后重试') from exc
    if len(content) > MAX_PDF:
        raise HTTPException(413, 'PDF 超过 80 MB 限制')
    if not content.startswith(b'%PDF'):
        raise HTTPException(422, '所选附件不是有效 PDF，请检查 Zotero 本地副本')
    return content, attachment['filename']


def preview(directory, config, item, attachments):
    with _preview_lock:
        now = time.monotonic()
        for key, old in list(_previews.items()):
            if now - old['time'] > 600:
                _previews.pop(key)
        while len(_previews) >= 100:
            _previews.pop(next(iter(_previews)))
        pid = uuid.uuid4().hex
        _previews[pid] = {'time': now, 'directory': str(Path(directory).resolve()),
                          'configuration_id': config['configuration_id'],
                          'item': copy.deepcopy(item), 'attachments': copy.deepcopy(attachments)}
        return pid


def get_preview(directory, config, pid):
    with _preview_lock:
        record = _previews.get(pid)
        if not record or record['directory'] != str(Path(directory).resolve()) or time.monotonic() - record['time'] > 600:
            raise HTTPException(409, '文献预览已过期，请重新选择该条目')
        if record['configuration_id'] != config['configuration_id']:
            raise HTTPException(409, 'Zotero 连接设置已更改，请重新选择该条目')
        return copy.deepcopy(record)
