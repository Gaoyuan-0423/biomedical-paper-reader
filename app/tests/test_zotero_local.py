"""Synthetic Zotero profile, including committed and uncommitted WAL frames."""
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_documents import fixture_pdf, main, TestClient
import zotero_local as zl


class LocalMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.profile = root / 'Zotero'
        self.profile.mkdir()
        db = self.profile / 'zotero.sqlite'
        self.db = sqlite3.connect(db)
        self.addCleanup(self.db.close)
        self.db.executescript("""
            PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;
            CREATE TABLE items(itemID INTEGER PRIMARY KEY,itemTypeID,libraryID,key);
            CREATE TABLE itemTypes(itemTypeID,typeName);
            CREATE TABLE fields(fieldID,fieldName);
            CREATE TABLE itemData(itemID,fieldID,valueID);
            CREATE TABLE itemDataValues(valueID,value);
            CREATE TABLE itemNotes(itemID,parentItemID,note);
            CREATE TABLE itemAttachments(itemID,parentItemID,linkMode,contentType,path);
            CREATE TABLE creators(creatorID,firstName,lastName);
            CREATE TABLE itemCreators(itemID,creatorID,creatorTypeID,orderIndex);
            CREATE TABLE deletedItems(itemID);
            CREATE TABLE groups(libraryID,groupID); INSERT INTO groups VALUES(2,1001);
            CREATE TABLE settings(setting,key,value); INSERT INTO settings VALUES('account','localUserKey','SYNTH123');
            INSERT INTO itemTypes VALUES(1,'journalArticle'),(2,'note'),(3,'attachment');
            INSERT INTO fields VALUES(1,'title'),(2,'DOI');
            INSERT INTO items VALUES(1,1,1,'MAINKEY1'),(2,2,1,'NOTEKEY1'),(3,3,1,'FILEKEY1'),
              (4,3,1,'FILEKEY2'),(5,3,1,'FILEKEY3'),(6,2,1,'DELETEDN'),(7,3,1,'FILEKEY4'),
              (8,3,1,'FILEKEY5'),(9,3,1,'FILEKEY6'),(11,1,2,'MAINKEY1'),(12,1,1,'TRASHED1');
            INSERT INTO itemData VALUES(1,1,1),(1,2,2),(11,1,3);
            INSERT INTO itemDataValues VALUES(1,'Local WAL paper'),(2,'10.1234/local'),(3,'Group paper');
            INSERT INTO creators VALUES(1,'Dana','Lee');
            INSERT INTO itemCreators VALUES(1,1,1,0);
            INSERT INTO itemNotes VALUES(2,1,'<p>My <b>Original</b> observation.</p><script>evil()</script>'),
             (6,1,'Deleted note');
            INSERT INTO deletedItems VALUES(6),(12);
            INSERT INTO itemAttachments VALUES(3,1,0,'application/pdf','storage:main.pdf'),
            (4,1,1,'application/pdf','storage:sup.pdf'),(5,1,2,'application/pdf','/external/file.pdf'),
            (7,1,0,'application/pdf','storage:../outside.pdf'),(8,1,0,'application/pdf','storage:missing.pdf'),
            (9,1,0,'application/pdf','storage:alias.pdf');
        """)
        for key, label in [('FILEKEY1','Main'),('FILEKEY2','Supp')]:
            folder = self.profile / 'storage' / key
            folder.mkdir(parents=True)
            (folder / ('main.pdf' if label == 'Main' else 'sup.pdf')).write_bytes(fixture_pdf(marker=label))
        linked = self.profile/'storage'/'FILEKEY6'
        linked.mkdir()
        (linked/'alias.pdf').symlink_to(self.profile/'storage'/'FILEKEY1'/'main.pdf')
        # The following change is uncommitted and must never enter a preview.
        self.db.execute("UPDATE itemDataValues SET value='Uncommitted' WHERE valueID=1")
        data = root/'BioReader'
        for key,value in [('DATA_DIR',data),('PAPERS_DIR',data/'papers'),('TRASH_DIR',data/'trash')]:
            override=patch.object(main,key,value); override.start(); self.addCleanup(override.stop)
        self.client=TestClient(main.app); self.addCleanup(self.client.close)
        self.scope=self.client.post('/api/project-libraries',json={'name':'Migration'}).json()['id']
        self.tokens=[]
        self.addCleanup(lambda: [zl.close_session(data,t) for t in self.tokens])

    def open(self, path=None):
        result=self.client.post('/api/zotero-local/preview',json={'path':str(path or self.profile),'library_id':self.scope})
        self.assertEqual(result.status_code,200,result.text)
        obj=result.json(); self.tokens.append(obj['preview'])
        return obj

    def run_import(self, obj, **selection):
        return self.client.post('/api/zotero-local/'+obj['preview']+'/import',json={'item_id':1,**selection})

    def raw_fingerprints(self):
        result = {}
        for path in self.profile.glob('zotero.sqlite*'):
            info = path.stat()
            # Windows byte-locks parts of the live WAL index. The importer
            # never copies that index. Check its presence and metadata only.
            signature = (info.st_size, info.st_mtime_ns)
            if path.name != 'zotero.sqlite-shm':
                signature += (hashlib.sha256(path.read_bytes()).hexdigest(),)
            result[path.name] = signature
        return result

    def test_wal_source_and_selected_children(self):
        before=self.raw_fingerprints()
        self.assertIn('zotero.sqlite',before)
        self.assertIn('zotero.sqlite-wal',before)
        self.assertIn('zotero.sqlite-shm',before)
        obj=self.open()
        self.assertEqual([i['title'] for i in obj['items']],['Local WAL paper','Group paper'])
        first=obj['items'][0]
        self.assertEqual(first['authors'],'Dana Lee')
        self.assertEqual(first['select_uri'],'zotero://select/library/items/MAINKEY1')
        self.assertEqual(obj['items'][1]['select_uri'],'zotero://select/groups/1001/items/MAINKEY1')
        self.assertEqual(first['notes'],[{'key':'NOTEKEY1','text':'My Original observation.'}])
        self.assertEqual([a['key'] for a in first['attachments'] if a['available']],['FILEKEY1','FILEKEY2'])
        self.assertFalse(main.list_meta())
        self.assertEqual(self.run_import(obj,notes=['unknown']).status_code,422)
        self.assertEqual(self.run_import(obj,attachments=['FILEKEY3']).status_code,409)
        self.assertFalse(main.list_meta())
        select={'notes':['NOTEKEY1'],'attachments':['FILEKEY1','FILEKEY2'],'main_attachment':'FILEKEY1'}
        result=self.run_import(obj,**select)
        self.assertEqual(result.status_code,200,result.text)
        paper=result.json()['paper']
        pid=paper['id']
        self.assertTrue(result.json()['in_library'])
        self.assertNotIn('zotero_local_notes',paper)
        self.assertEqual(paper['filename'],'main.pdf')
        self.assertEqual(paper['zotero_local_origins'][0]['select_uri'],first['select_uri'])
        self.assertEqual([s['filename'] for s in paper['supplements']],['sup.pdf'])
        self.assertEqual(self.client.get('/api/papers/'+pid).json()['zotero_local_notes'][0]['key'],'NOTEKEY1')
        self.assertEqual(main.pl.active_project(main.DATA_DIR,self.scope).paper_ids,[pid])
        again=self.run_import(obj,**select)
        self.assertEqual(again.status_code,200,again.text)
        self.assertTrue(again.json()['existing'])
        self.assertEqual(len(main.read_meta(main.paper_dir(pid))['zotero_local_pdf_sources']),2)
        self.assertEqual(len(main.list_meta()),1)
        self.assertEqual(self.raw_fingerprints(),before)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows mandatory file locking')
    def test_windows_locked_database_can_be_retried_after_release(self):
        import msvcrt
        source = self.profile / 'zotero.sqlite'
        with source.open('r+b', buffering=0) as occupied:
            msvcrt.locking(occupied.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                response = self.client.post('/api/zotero-local/preview', json={'path': str(source), 'library_id': self.scope})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertIn('退出 Zotero', response.json()['detail'])
            finally:
                occupied.seek(0)
                msvcrt.locking(occupied.fileno(), msvcrt.LK_UNLCK, 1)
        self.assertEqual(self.open()['items'][0]['title'], 'Local WAL paper')

    def test_existing_doi_preserves_notes_and_main(self):
        res=self.client.post('/api/papers',files={'file':('existing.pdf',fixture_pdf(marker='Existing'))})
        pid=res.json()['id']; directory=main.paper_dir(pid)
        meta=main.read_meta(directory); meta['doi']='https://doi.org/10.1234/LOCAL'
        meta['notes']='A manual note'; main.write_meta(directory,meta)
        pdf=(directory/'paper.pdf').read_bytes()
        obj=self.open(self.profile/'zotero.sqlite')
        self.assertEqual(obj['items'][0]['existing_id'],pid)
        reply=self.run_import(obj,notes=['NOTEKEY1'],attachments=['FILEKEY1'],main_attachment='FILEKEY1')
        self.assertEqual(reply.status_code,200,reply.text)
        meta=main.read_meta(directory)
        self.assertEqual(meta['notes'],'A manual note')
        self.assertEqual(meta['zotero_local_notes'][0]['text'],'My Original observation.')
        self.assertEqual((directory/'paper.pdf').read_bytes(),pdf)
        self.assertEqual(len(meta['supplements']),1)
        self.assertNotEqual(obj['items'][0]['identity'],obj['items'][1]['identity'])
        group=self.client.post('/api/zotero-local/'+obj['preview']+'/import',json={'item_id':11})
        self.assertEqual(group.status_code,200,group.text)
        self.assertNotEqual(group.json()['paper']['id'],pid)

    def test_paging_and_restore_required_for_trashed_target(self):
        self.db.rollback()
        self.db.executemany("INSERT INTO items VALUES(?,1,1,?)", [(i,f'P{i:07}') for i in range(20,44)])
        self.db.commit()
        obj=self.open()
        self.assertEqual(len(obj['items']),20); self.assertTrue(obj['has_more'])
        endpoint='/api/zotero-local/'+obj['preview']
        self.assertEqual(self.client.post(endpoint+'/import',json={'item_id':43}).status_code,409)
        last=self.client.get(endpoint,params={'offset':20}).json()
        self.assertFalse(last['has_more'])
        self.assertEqual(len({r['id'] for r in [*obj['items'],*last['items']]}),26)
        result=self.client.post(endpoint+'/import',json={'item_id':43}).json()
        self.client.delete('/api/papers/'+result['paper']['id'])
        self.assertTrue(self.client.get(endpoint,params={'offset':20}).json()['items'][-1]['trashed'])
        self.assertEqual(self.client.post(endpoint+'/import',json={'item_id':43}).status_code,409)

    def test_invalid_second_pdf_rolls_back_the_paper(self):
        (self.profile/'storage'/'FILEKEY2'/'sup.pdf').write_bytes(b'%PDF-broken')
        obj=self.open()
        result=self.run_import(obj,notes=['NOTEKEY1'],attachments=['FILEKEY1','FILEKEY2'],main_attachment='FILEKEY1')
        self.assertGreaterEqual(result.status_code,400)
        self.assertEqual(main.list_meta(),[])
        self.assertEqual(main.pl.active_project(main.DATA_DIR,self.scope).paper_ids,[])

    def test_expired_or_changed_material_and_archived_scope(self):
        obj=self.open()
        (self.profile/'storage'/'FILEKEY1'/'main.pdf').write_bytes(fixture_pdf(marker='Changed'))
        self.assertEqual(self.run_import(obj,attachments=['FILEKEY1']).status_code,409)
        self.assertFalse(main.list_meta())
        self.client.delete('/api/zotero-local/'+obj['preview'])
        self.assertEqual(self.run_import(obj).status_code,409)
        fresh=self.open()
        project=main.pl.active_project(main.DATA_DIR,self.scope); project.archived=True
        main.pl.persist(main.DATA_DIR,project)
        self.assertEqual(self.run_import(fresh).status_code,409)
        self.assertFalse(main.list_meta())


if __name__ == '__main__':
    unittest.main()
