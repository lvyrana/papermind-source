import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch, AsyncMock
from vercel.blob import BlobNotFoundError

from fastapi import HTTPException
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "papermind"))
import cloud_files as files


class CloudFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "files.db"
        conn = self.connect()
        conn.executescript("CREATE TABLE saved_papers(id INTEGER PRIMARY KEY,user_id TEXT,has_pdf INTEGER); INSERT INTO saved_papers VALUES(1,'alice',0);" + files.SCHEMA)
        conn.close()
        self.patcher = patch.object(files.database, "_ensure_db", self.connect)
        self.patcher.start()

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def test_owner_quota_and_idempotent_reservation(self):
        with self.assertRaises(HTTPException) as error:
            files.reserve(1, 'bob', 10)
        self.assertEqual(error.exception.status_code, 404)
        first = files.reserve(1, 'alice', 10)
        self.assertEqual(files.reserve(1, 'alice', 10)['id'], first['id'])
        with self.assertRaises(HTTPException):
            files.reserve(1, 'alice', 11)
        with self.assertRaises(HTTPException):
            files.reserve(1, 'alice', files.MAX_PDF + 1)
        with self.assertRaises(HTTPException):
            files.current(1, 'alice')

    def test_unfinished_and_retired_uploads_still_consume_quota(self):
        with self.connect() as conn:
            conn.execute("INSERT INTO pdf_uploads VALUES('full',2,'alice','reserved.pdf',?,'retired',0,0,NULL)", (100 * 1024 * 1024,))
        with self.assertRaises(HTTPException) as error:
            files.reserve(1, 'alice', 10)
        self.assertEqual(error.exception.status_code, 413)

    def run_complete(self, content, row):
        class Download:
            metadata = SimpleNamespace(content_type='application/pdf')
            def __aiter__(self):
                async def chunks():
                    yield content[:3]
                    yield content[3:]
                return chunks()
        @asynccontextmanager
        async def stream(pathname, **kwargs):
            self.assertEqual(pathname, row['pathname'])
            self.assertEqual(kwargs, {'access': 'private'})
            yield Download()
        with patch('vercel.blob.stream_blob', stream):
            return asyncio.run(files.complete(1, 'alice', row['id']))

    def test_verified_file_persists_and_other_user_is_denied(self):
        row = files.reserve(1, 'alice', 9)
        result = self.run_complete(b'%PDF-test', row)
        self.assertTrue(result['ok'])
        self.assertEqual(files.current(1, 'alice')['sha256'], result['sha256'])
        self.assertTrue(self.run_complete(b'not used', row)['ok'])
        with self.assertRaises(HTTPException):
            files.current(1, 'bob')
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT has_pdf FROM saved_papers').fetchone()[0], 1)

    def test_invalid_pdf_never_marks_ready(self):
        row = files.reserve(1, 'alice', 9)
        with self.assertRaises(HTTPException):
            self.run_complete(b'not-a-pdf', row)
        with self.assertRaises(HTTPException):
            files.current(1, 'alice')
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT has_pdf FROM saved_papers').fetchone()[0], 0)

    def expire(self, row):
        with self.connect() as conn:
            conn.execute('UPDATE pdf_uploads SET expires=? WHERE id=?', (int(time.time()) - files.GC_GRACE - 5, row['id']))

    def sweep(self, *, delete_error=None, still_exists=False, uid='alice'):
        delete = AsyncMock(side_effect=delete_error)
        head = AsyncMock(side_effect=None if still_exists else BlobNotFoundError('absent'))
        with patch('vercel.blob.delete_blob', delete), patch('vercel.blob.head_blob', head):
            result = asyncio.run(files.cleanup(uid))
        return result, delete

    def test_retire_revokes_read_and_completion_but_waits_for_upload_expiry(self):
        row = files.reserve(1, 'alice', 9)
        self.run_complete(b'%PDF-test', row)
        files.retire(1, 'alice')
        with self.assertRaises(HTTPException):
            files.current(1, 'alice')
        with self.assertRaises(HTTPException):
            asyncio.run(files.complete(1, 'alice', row['id']))
        result, delete = self.sweep()
        self.assertEqual(result['cleanup_pending'], 1)
        delete.assert_not_called()
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT has_pdf FROM saved_papers').fetchone()[0], 0)
        self.assertNotEqual(files.reserve(1, 'alice', 9)['id'], row['id'])

    def test_expired_pending_reclaims_quota_only_after_confirmed_absence(self):
        row = files.reserve(1, 'alice', 9)
        self.expire(row)
        for kwargs in ({'delete_error': RuntimeError('storage offline')}, {'still_exists': True}):
            result, _ = self.sweep(**kwargs)
            self.assertEqual(result['failed'], 1)
            with self.connect() as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM pdf_cleanup').fetchone()[0], 0)
        result, delete = self.sweep()
        self.assertEqual(result['reclaimed'], 1)
        delete.assert_awaited_once_with(row['pathname'])
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM pdf_cleanup').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT state FROM pdf_uploads').fetchone()[0], 'retired')

    def test_ready_and_other_account_files_never_swept(self):
        row = files.reserve(1, 'alice', 9)
        self.run_complete(b'%PDF-test', row)
        self.expire(row)
        _, delete = self.sweep()
        delete.assert_not_called()
        with self.assertRaises(HTTPException):
            files.retire(1, 'bob')
        files.retire(1, 'alice')
        _, delete = self.sweep(uid='bob')
        delete.assert_not_called()

    def test_reclaimed_tombstone_still_cleans_late_objects(self):
        row = files.reserve(1, 'alice', 9)
        self.expire(row)
        self.sweep()
        _, delete = self.sweep()
        delete.assert_not_called()
        with self.connect() as conn:
            conn.execute('UPDATE pdf_cleanup SET checked_at=0')
        result, delete = self.sweep()
        delete.assert_awaited_once_with(row['pathname'])
        self.assertEqual(result['reclaimed'], 1)

    def test_invalid_ledger_path_is_not_sent_to_storage(self):
        row = files.reserve(1, 'alice', 9)
        self.expire(row)
        with self.connect() as conn:
            conn.execute("UPDATE pdf_uploads SET pathname='unrelated/private.pdf'")
        result, delete = self.sweep()
        delete.assert_not_called()
        self.assertEqual(result['failed'], 1)

    def test_delete_during_verification_cannot_resurrect_file(self):
        row = files.reserve(1, 'alice', 9)
        class Download:
            metadata = SimpleNamespace(content_type='application/pdf')
            def __aiter__(inner):
                async def chunks():
                    files.retire(1, 'alice')
                    yield b'%PDF-test'
                return chunks()
        @asynccontextmanager
        async def stream(*args, **kwargs):
            yield Download()
        with patch('vercel.blob.stream_blob', stream), self.assertRaises(HTTPException) as error:
            asyncio.run(files.complete(1, 'alice', row['id']))
        self.assertEqual(error.exception.status_code, 409)
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT has_pdf FROM saved_papers').fetchone()[0], 0)

    def test_paper_delete_rolls_back_retirement_if_related_row_delete_fails(self):
        row = files.reserve(1, 'alice', 9)
        self.run_complete(b'%PDF-test', row)
        with patch.object(files.database, '_delete_saved_paper_rows', side_effect=ValueError('failure')):
            with self.assertRaises(ValueError):
                files.retire(1, 'alice', delete_paper=True)
        self.assertEqual(files.current(1, 'alice')['id'], row['id'])

    def test_quota_excludes_only_confirmed_cleanup(self):
        row = files.reserve(1, 'alice', 9)
        self.expire(row)
        with self.connect() as conn:
            conn.execute('UPDATE pdf_uploads SET size=?', (100 * 1024 * 1024,))
        with self.assertRaises(HTTPException):
            files.reserve(1, 'alice', 9)
        self.sweep()
        self.assertTrue(files.reserve(1, 'alice', 9)['id'])
