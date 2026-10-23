import sqlite3
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from initialize_preview_files import migrate
from cloud_files import SCHEMA, GC_SCHEMA


class FileSchemaTests(unittest.TestCase):
    def test_v1_upgrade_preserves_upload_ledger(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.executescript("CREATE TABLE _papermind_preview_init(purpose TEXT,role TEXT); INSERT INTO _papermind_preview_init VALUES('litfinch-synthetic-business-v1','library');")
        conn.executescript(SCHEMA.replace(GC_SCHEMA, '').replace('VALUES (2)', 'VALUES (1)'))
        conn.execute("INSERT INTO pdf_uploads VALUES('old',1,'reader','keep.pdf',10,'ready',0,0,'hash')")
        conn.commit()
        self.assertEqual(migrate(conn), 'upgraded_to_2')
        self.assertEqual(conn.execute('SELECT pathname FROM pdf_uploads').fetchone()[0], 'keep.pdf')
        self.assertEqual(migrate(conn), 'already_prepared')

    def test_unmarked_database_is_never_changed(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.execute('CREATE TABLE existing(data TEXT)')
        with self.assertRaises(sqlite3.OperationalError):
            migrate(conn)
        self.assertEqual(conn.execute('SELECT name FROM sqlite_master').fetchall(), [('existing',)])
