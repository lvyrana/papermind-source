from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import import_cloud_production as migration
from cloud_files import SCHEMA as PDF_SCHEMA
from cloud_figures import SCHEMA as FIGURE_SCHEMA


class ImportTests(unittest.TestCase):
    def test_target_refuses_unrelated_data_and_different_import(self):
        with closing(sqlite3.connect(':memory:')) as conn:
            self.assertEqual(migration.target_state(conn,'library','abc'),'empty')
            conn.execute('CREATE TABLE records(value TEXT)')
            with self.assertRaises(ValueError): migration.target_state(conn,'library','abc')
            conn.execute('CREATE TABLE _papermind_production_import(role TEXT,fingerprint TEXT)')
            conn.execute("INSERT INTO _papermind_production_import VALUES('library','abc')")
            self.assertEqual(migration.target_state(conn,'library','abc'),'imported')
            with self.assertRaises(ValueError): migration.target_state(conn,'library','new')

    def test_file_plan_maps_owner_ids_and_checksums_without_touching_source(self):
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder)
            (data/'pdfs').mkdir(); (data/'figures').mkdir()
            (data/'pdfs/7.pdf').write_bytes(b'%PDF-1.4 synthetic')
            from PIL import Image
            Image.new('RGB',(4,4),'white').save(data/'figures/old.png')
            (data/'PREPARATION-RECEIPT.json').write_text(json.dumps({'format':'papermind-turso-preparation-v1','uploaded':False,'source_snapshot':'synthetic'}))
            with closing(sqlite3.connect(data/'paperdiary.db')) as conn:
                conn.executescript('CREATE TABLE saved_papers(id INTEGER,user_id TEXT,has_pdf INTEGER); CREATE TABLE board_items(id INTEGER,paper_rowid INTEGER,image TEXT,section TEXT,page INTEGER,content TEXT);'+PDF_SCHEMA+FIGURE_SCHEMA)
                conn.execute("INSERT INTO saved_papers VALUES(7,'owner',1)")
                conn.execute("INSERT INTO board_items VALUES(9,7,'old.png','method',1,'caption')")
                conn.commit()
            plan=migration.attachment_plan(data)
            self.assertEqual(plan,migration.attachment_plan(data))
            self.assertEqual(len(plan),2)
            migration.map_files(data,plan)
            with closing(sqlite3.connect(data/'paperdiary.db')) as conn:
                self.assertEqual(conn.execute('SELECT paper_id,user_id,state FROM pdf_uploads').fetchall(),[(7,'owner','ready')])
                self.assertEqual(conn.execute('SELECT paper_id,user_id,state,item_id FROM figure_uploads').fetchall(),[(7,'owner','ready',9)])
                self.assertEqual(conn.execute('SELECT image FROM board_items').fetchone()[0],plan[1]['name'])
            self.assertTrue((data/'figures/old.png').exists())

    def test_preview_store_or_database_cannot_receive_real_data(self):
        with patch.dict(os.environ,{'BLOB_STORE_ID':'store_RkRS2huB7kCab3j8'}):
            with self.assertRaises(ValueError): migration.validate_targets()
        with patch.dict(os.environ,{'BLOB_STORE_ID':migration.BLOB}),patch.object(migration,'remote_credentials',return_value=('libsql://litfinch-library-preview-test.turso.io','hidden')):
            with self.assertRaises(ValueError): migration.validate_targets()

    def test_row_checksum_ignores_order_not_ownership(self):
        self.assertEqual(migration.row_hash({'a':[[1,'owner'],[2,b'file']]}),migration.row_hash({'a':[[2,b'file'],[1,'owner']]}))
        self.assertNotEqual(migration.row_hash({'a':[[1,'owner']]}),migration.row_hash({'a':[[1,'stranger']]}))

    def test_dump_creates_parents_before_loading_children_with_fk_checks_enabled(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'library.db'
            with closing(sqlite3.connect(path)) as conn:
                conn.executescript("CREATE TABLE _papermind_schema(role TEXT,version INTEGER); INSERT INTO _papermind_schema VALUES('library',1); CREATE TABLE saved_papers(id INTEGER PRIMARY KEY); INSERT INTO saved_papers VALUES(4); CREATE TABLE board_items(id INTEGER PRIMARY KEY,paper_rowid INTEGER REFERENCES saved_papers(id)); INSERT INTO board_items VALUES(2,4);")
            dump,_,_=migration.local_export(path,'library')
            with closing(sqlite3.connect(':memory:')) as target:
                target.execute('PRAGMA foreign_keys=ON')
                target.executescript('BEGIN IMMEDIATE; PRAGMA defer_foreign_keys=ON;'+dump+'COMMIT;')
                self.assertEqual(target.execute('SELECT * FROM board_items').fetchall(),[(2,4)])
                self.assertEqual(target.execute('PRAGMA foreign_key_check').fetchall(),[])
