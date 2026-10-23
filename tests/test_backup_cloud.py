from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import backup_cloud as backup


def result(values):
    def cell(value):
        return {'type':'null'} if value is None else {'type':'integer' if isinstance(value,int) else 'text','value':str(value)}
    return {'rows':[[cell(value) for value in row] for row in values]}


class CloudBackupTests(unittest.TestCase):
    def test_capture_excludes_secrets_and_sessions_from_queries(self):
        schema = [['table','_papermind_schema','_papermind_schema','CREATE TABLE _papermind_schema(role TEXT,version INTEGER)'],
                  ['table','accounts','accounts','CREATE TABLE accounts(id TEXT)'],
                  ['table','sessions','sessions','CREATE TABLE sessions(hash TEXT)']]
        def query(role,statements):
            self.assertFalse(any('SELECT * FROM "sessions"' in sql for sql in statements))
            if len(statements) == 1: return [result(schema)]
            return [result([]),result(schema),result([['auth',1]]),result([['synthetic']]),result([])]
        with patch.object(backup,'pipeline',query):
            captured = backup.capture('auth')
        self.assertNotIn('sessions',captured['data'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'auth.db'
            backup.write_database(path,captured)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM sessions').fetchone()[0],0)
                self.assertEqual(conn.execute('SELECT id FROM accounts').fetchone()[0],'synthetic')

    def test_unknown_tables_and_changed_schema_fail_closed(self):
        with patch.object(backup,'pipeline',return_value=[result([['table','private_unknown','private_unknown','CREATE TABLE private_unknown(x)']])]):
            with self.assertRaises(ValueError): backup.capture('auth')
        schema = [['table','_papermind_schema','_papermind_schema','CREATE TABLE _papermind_schema(role TEXT,version INTEGER)']]
        with patch.object(backup,'pipeline',side_effect=[[result(schema)],[result([]),result([]),result([]),result([])]]):
            with self.assertRaises(ValueError): backup.capture('auth')

    def test_untrusted_schema_cannot_attach_local_files(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = {'schema':[['table','accounts','accounts',"ATTACH DATABASE '/tmp/not-authorized.db' AS other"]],'data':{}}
            with self.assertRaises(sqlite3.DatabaseError): backup.write_database(Path(directory)/'test.db',snapshot)

    def test_fingerprint_ignores_row_order_but_detects_content_changes(self):
        first = {'schema':[],'data':{'sample':[[1,b'bytes'],[2,None]]}}
        second = {'schema':[],'data':{'sample':[[2,None],[1,b'bytes']]}}
        self.assertEqual(backup.fingerprint(first),backup.fingerprint(second))
        second['data']['sample'][0][0] = 3
        self.assertNotEqual(backup.fingerprint(first),backup.fingerprint(second))

    def test_snapshot_query_is_read_only(self):
        schema = [['table','_papermind_schema','_papermind_schema','CREATE TABLE _papermind_schema(role TEXT,version INTEGER)']]
        statements_seen = []
        def query(role,statements):
            statements_seen.extend(statements)
            if len(statements) == 1: return [result(schema)]
            return [result([]),result(schema),result([['auth',1]]),result([])]
        with patch.object(backup,'pipeline',query): backup.capture('auth')
        self.assertEqual(statements_seen[1],'BEGIN DEFERRED')
        self.assertEqual(statements_seen[-1],'ROLLBACK')
        self.assertFalse(any(sql.startswith(('INSERT','UPDATE','DELETE')) for sql in statements_seen))

    def test_autoincrement_high_water_mark_survives_deleted_rows(self):
        snapshot = {'schema':[['table','sample','sample','CREATE TABLE sample(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT)']],
                    'data':{'sample':[[1,'kept']],'sqlite_sequence':[['sample',50]]}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'library.db'
            backup.write_database(path,snapshot)
            with closing(sqlite3.connect(path)) as conn:
                row = conn.execute("INSERT INTO sample(name) VALUES('next')")
                self.assertEqual(row.lastrowid,51)
