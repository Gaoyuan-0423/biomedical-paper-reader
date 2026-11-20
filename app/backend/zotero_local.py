"""Read a Zotero library snapshot and expose only the reader's selected children."""
from contextlib import contextmanager
import hashlib
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import threading
import time
import uuid
from html.parser import HTMLParser
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, Field

import zotero_import as zi

PAGE_SIZE = 20
_sessions = {}
_lock = threading.RLock()
fields = {'title': 'title', 'DOI': 'doi', 'date': 'year', 'publicationTitle': 'journal',
          'abstractNote': 'abstract', 'url': 'url'}


class OpenIn(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    library_id: str = Field(default='all', max_length=32)


class ImportIn(BaseModel):
    item_id: int = Field(gt=0, strict=True)
    notes: list[str] = Field(default_factory=list, max_length=250)
    attachments: list[str] = Field(default_factory=list, max_length=250)
    main_attachment: str = Field(default='', max_length=8)


class NoteText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        if not self.hidden and tag in ('br', 'p', 'div', 'li', 'tr', 'h1', 'h2', 'h3'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in ('p', 'div', 'li', 'tr'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

    @classmethod
    def plain(cls, note):
        parser = cls()
        parser.feed(note or '')
        parser.close()
        return '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())


def fingerprint(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise HTTPException(422, '数据库和日志必须是普通本地文件。')
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def snapshot(source, destination):
    # SQLite may create or write -shm even for read-only WAL connections.
    # Copy a stable generation first and let SQLite checkpoint only the private copy.
    originals = [source, Path(str(source) + '-wal'), Path(str(source) + '-journal')]
    for attempt in range(3):
        before = [fingerprint(p) for p in originals]
        if not before[0]:
            raise HTTPException(409, 'Zotero 数据库正在变化，请稍后重新预览。')
        for i, path in enumerate(originals):
            dest = destination.with_name(destination.name + ('', '-wal', '-journal')[i])
            dest.unlink(missing_ok=True)
            if before[i] is None:
                continue
            try:
                fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                with os.fdopen(fd, 'rb') as src, dest.open('wb') as out:
                    if not stat.S_ISREG(os.fstat(src.fileno()).st_mode):
                        raise HTTPException(422, '数据库和日志必须是普通本地文件。')
                    shutil.copyfileobj(src, out, 1024 * 1024)
            except FileNotFoundError:
                break
        else:
            if before == [fingerprint(p) for p in originals]:
                return
    raise HTTPException(409, '复制时 Zotero 数据库发生变化，请稍后重试。')


def open_session(data, body):
    source = Path(body.path.strip()).expanduser()
    if not source.is_absolute():
        raise HTTPException(422, '请选择 Zotero 资料目录或 zotero.sqlite 的绝对路径。')
    if source.is_dir():
        source /= 'zotero.sqlite'
    if source.name.lower() != 'zotero.sqlite' or source.is_symlink() or not source.is_file():
        raise HTTPException(422, '所选目录中未找到普通文件 zotero.sqlite。')
    source = source.resolve()
    temporary = tempfile.TemporaryDirectory(prefix='bioreader-zotero-readonly-')
    dbpath = Path(temporary.name) / 'zotero.sqlite'
    try:
        snapshot(source, dbpath)
        with closing_db(dbpath, writable=True) as con:
            if con.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:
                raise HTTPException(409, '无法合并 Zotero 日志，请重新预览。')
            # Reject incompatible or incomplete profiles before publishing the session.
            con.execute('SELECT i.key,t.typeName FROM items i JOIN itemTypes t USING(itemTypeID) LIMIT 0')
            con.execute('SELECT note FROM itemNotes LIMIT 0')
            con.execute('SELECT path,linkMode FROM itemAttachments LIMIT 0')
            con.execute('SELECT fieldName FROM fields LIMIT 0')
            con.execute('SELECT itemID FROM deletedItems LIMIT 0')
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            account = dict(con.execute("SELECT key,value FROM settings WHERE setting='account' AND key IN ('userID','localUserKey')")) if 'settings' in tables else {}
            profile = account.get('userID') or account.get('localUserKey')
            seed = 'account:' + str(profile) if profile else 'path:' + str(source)
            groups = dict(con.execute('SELECT libraryID,groupID FROM groups')) if 'groups' in tables else {}
            users = {r[0] for r in con.execute("SELECT libraryID FROM libraries WHERE type='user'")} if 'libraries' in tables else {1}
        rec = {'db': dbpath, 'temp': temporary, 'profile': hashlib.sha256(os.fsencode(seed)).hexdigest(), 'groups':groups, 'users':users, 'user_id':account.get('userID'),
               'storage': source.parent / 'storage', 'scope': body.library_id, 'owner': str(Path(data).resolve()),
               'last': time.monotonic(), 'previewed': {}}
        token = uuid.uuid4().hex
        with _lock:
            expire()
            if len(_sessions) >= 8:
                raise HTTPException(409, '同时打开的本地文库预览过多，请关闭旧预览后重试。')
            _sessions[token] = rec
        return token
    except BaseException as exc:
        temporary.cleanup()
        if isinstance(exc, (OSError, sqlite3.Error)):
            raise HTTPException(422, '无法读取 Zotero 数据文件，请检查是否选中完整资料目录。') from exc
        raise


@contextmanager
def closing_db(path, writable=False):
    uri = path.as_uri() + ('?mode=rw' if writable else '?mode=ro&immutable=1')
    con = sqlite3.connect(uri, uri=True)
    try:
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA trusted_schema=OFF')
        yield con
    finally:
        con.close()


def expire():
    now = time.monotonic()
    for token, rec in list(_sessions.items()):
        if now - rec['last'] > 1800:
            _sessions.pop(token)['temp'].cleanup()


def session(data, token):
    expire()
    rec = _sessions.get(token)
    if not rec or rec['owner'] != str(Path(data).resolve()):
        raise HTTPException(409, '本地文库预览已过期，请重新打开。')
    rec['last'] = time.monotonic()
    return rec


def close_session(data, token):
    with _lock:
        if token in _sessions:
            session(data, token)
            _sessions.pop(token)['temp'].cleanup()


base_query = """SELECT i.itemID,i.libraryID,i.key,t.typeName FROM items i JOIN itemTypes t USING(itemTypeID)
WHERE t.typeName NOT IN ('note','attachment','annotation') AND NOT EXISTS
(SELECT 1 FROM deletedItems x WHERE x.itemID=i.itemID)"""


def describe(con, row, rec):
    item_id = row['itemID']
    if type(item_id) is not int or type(row['libraryID']) is not int or not zi.KEY.fullmatch(str(row['key'] or '')):
        raise HTTPException(422, 'Zotero 条目键或文库编号无效。')
    item = {'id': item_id, 'key': row['key'], 'library_id': row['libraryID'], 'library_type': 'local',
            'type': row['typeName'], 'identity': f"{rec['profile']}:{row['libraryID']}:{row['key']}"}
    group = rec['groups'].get(row['libraryID'])
    if zi.KEY.fullmatch(row['key']) and (row['libraryID'] in rec['users'] or (type(group) is int and group > 0)):
        path = 'groups/' + str(group) if group else 'library'
        item['select_uri'] = f"zotero://select/{path}/items/{item['key']}"
        if group or rec['user_id']:
            item['online_library'] = {'library_type': 'group' if group else 'user', 'library_id': str(group or rec['user_id'])}
        if group:
            item['identity'] = f"group:{group}:{item['key']}"
    for field, value in con.execute("""SELECT f.fieldName,v.value FROM itemData d
        JOIN fields f USING(fieldID) JOIN itemDataValues v USING(valueID) WHERE d.itemID=?""", (item_id,)):
        if field in fields:
            item[fields[field]] = str(value or '')
    date = re.search(r'\b(?:1[0-9]{3}|20[0-9]{2})\b', item.get('year', ''))
    item['year'] = date[0] if date else ''
    authors = con.execute("""SELECT c.firstName,c.lastName FROM itemCreators a JOIN creators c USING(creatorID)
        WHERE a.itemID=? AND a.creatorTypeID=1 ORDER BY a.orderIndex""", (item_id,))
    item['authors'] = ', '.join(' '.join(part for part in author if part) for author in authors)
    item['title'] = item.get('title') or row['key']
    child = """JOIN items i ON i.itemID=c.itemID WHERE c.parentItemID=? AND i.libraryID=?
        AND NOT EXISTS (SELECT 1 FROM deletedItems x WHERE x.itemID=i.itemID) ORDER BY c.itemID"""
    args = (item_id, row['libraryID'])
    item['notes'] = [{'key': r[0], 'text': NoteText.plain(r[1])} for r in con.execute(
        'SELECT i.key,c.note FROM itemNotes c ' + child, args)]
    item['attachments'] = []
    for key, path, mime, link_mode in con.execute('SELECT i.key,c.path,c.contentType,c.linkMode FROM itemAttachments c ' + child, args):
        path = path or ''
        managed = path.startswith('storage:') and link_mode in (0, 1)
        attachment = {'key': key, 'filename': path[8:] if managed else path.replace('\\','/').split('/')[-1],
                      'content_type': mime, 'link_mode': 'imported_file' if managed else 'linked_file',
                      'library_type': 'local', 'library_id': row['libraryID'], 'parent_key': row['key']}
        try:
            if rec['storage'].is_symlink():
                raise HTTPException(422, '附件目录不能是符号链接。')
            zi.local_attachment_path(rec['storage'], item, attachment)
            reason = ''
        except HTTPException as exc:
            reason = str(exc.detail)
        item['attachments'].append({**attachment, 'available': not reason, 'reason': reason})
    return item


def duplicate(item, papers):
    exact = next((p for p in papers if item['identity'] in p.get('zotero_local_ids', [])), None)
    online = item.get('online_library')
    if online and not exact:
        for paper in papers:
            source = paper.get('zotero_source') or {}
            if (source.get('item_key') == item['key'] and source.get('library_type') == online['library_type']
                    and str(source.get('library_id')) == online['library_id']):
                return paper
    doi = zi._doi(item.get('doi'))
    return exact or next((p for p in papers if doi and zi._doi(p.get('doi')) == doi), None)


def read(data, token, offset=0, item_id=None):
    with _lock:
        rec = session(data, token)
        try:
            with closing_db(rec['db']) as con:
                if item_id:
                    row = con.execute(base_query + ' AND i.itemID=?', (item_id,)).fetchone()
                    if not row:
                        raise HTTPException(404, '预览中没有这个 Zotero 条目。')
                    if item_id not in rec['previewed']:
                        raise HTTPException(409, '请先预览这个 Zotero 条目。')
                    return describe(con, row, rec), rec['scope'], rec['storage'], rec['previewed'][item_id]
                rows = con.execute(base_query + ' ORDER BY i.itemID LIMIT ? OFFSET ?', (PAGE_SIZE + 1, offset)).fetchall()
                items = [describe(con, row, rec) for row in rows[:PAGE_SIZE]]
                for item in items:
                    rec['previewed'][item['id']] = {
                        a['key']: fingerprint(rec['storage'] / a['key'] / a['filename'])
                        for a in item['attachments'] if a['available']}
                return items, len(rows) > PAGE_SIZE, rec['scope']
        except sqlite3.Error as exc:
            raise HTTPException(422, '数据库缺少可读取的 Zotero 题录或附件字段。') from exc


def prepare(stage, item, body, storage, stamps, meta, add_main, add_supplement, existing_hashes):
    note_keys, attachments = set(body.notes), {a['key']: a for a in item['attachments']}
    notes = [n for n in item['notes'] if n['key'] in note_keys]
    if (len(notes) != len(body.notes) or len(set(body.attachments)) != len(body.attachments)
            or not set(body.attachments) <= attachments.keys() or body.main_attachment and body.main_attachment not in body.attachments):
        raise HTTPException(422, '所选笔记或附件不属于这篇预览文献。')
    identities = meta.setdefault('zotero_local_ids', [])
    if item['identity'] not in identities:
        identities.append(item['identity'])
    origins = meta.setdefault('zotero_local_origins', [])
    if not any(o['identity'] == item['identity'] for o in origins):
        origins.append({'identity':item['identity'], 'item_key':item['key'], 'library_id':item['library_id'],
                        'select_uri': item.get('select_uri', '')})
    saved_notes = meta.setdefault('zotero_local_notes', [])
    for note in notes:
        versions = [n for n in saved_notes if n['source'] == item['identity'] and n['key'] == note['key']]
        if not any(n['text'] == note['text'] for n in versions):
            saved_notes.append(dict(note, source=item['identity'], library_id=item['library_id'],
                                    item_key=item['key'], version=len(versions) + 1))
    sources = meta.setdefault('zotero_local_pdf_sources', [])
    for key in body.attachments:
        attachment = attachments[key]
        if not attachment['available'] or key not in stamps or storage.is_symlink():
            raise HTTPException(409, '所选 PDF 不可用，请重新预览。')
        path = storage / key / attachment['filename']
        if fingerprint(path) != stamps[key]:
            raise HTTPException(409, 'Zotero 附件已变化，请重新预览。')
        content = zi.read_local_pdf(zi.local_attachment_path(storage, item, attachment))
        name = attachment['filename']
        if fingerprint(path) != stamps[key]:
            raise HTTPException(409, '复制时 Zotero 附件已变化，请重新预览。')
        digest = hashlib.sha256(content).hexdigest()
        revision = meta.get('document_revisions', {})
        doc = existing_hashes.get(digest) or next((k for k, value in revision.items() if value == digest), None)
        if not doc:
            if key == body.main_attachment and not meta.get('has_file') and not revision.get('main'):
                add_main(stage, content, name, meta)
                doc = 'main'
            else:
                doc = uuid.uuid4().hex
                add_supplement(stage, content, name, doc, digest, meta)
        origin = dict(source=item['identity'], key=key, document_id=doc, sha256=digest, filename=name)
        if origin not in sources:
            sources.append(origin)
