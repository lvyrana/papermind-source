import asyncio
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'papermind'))
import cloud_jobs as jobs
from src import database
from fastapi import HTTPException


class JobTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'library.db'
        with closing(sqlite3.connect(self.path)) as conn:
            conn.executescript(jobs.SCHEMA + """
              CREATE TABLE saved_papers(id INTEGER PRIMARY KEY,user_id TEXT,title TEXT,abstract TEXT,summary_zh TEXT,relevance TEXT,category TEXT);
              INSERT INTO saved_papers VALUES(1,'alice','Title','Abstract','','','');
              CREATE TABLE user_profiles(user_id TEXT PRIMARY KEY,memory_core TEXT,core_source TEXT,last_core_merged_at TEXT,updated_at TEXT);
              INSERT INTO user_profiles VALUES('alice','','','','v1');
            """)
        for scope in (patch.dict(os.environ, {'PAPERMIND_SQL_BACKEND':'turso'}),
                      patch.object(database, '_ensure_db', side_effect=self.connect)):
            scope.start()
            self.addCleanup(scope.stop)

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def sql(self, query, params=()):
        with closing(self.connect()) as conn:
            rows = conn.execute(query, params).fetchall()
            conn.commit()
            return rows

    def test_dedup_ownership_and_restart(self):
        task = jobs.enqueue('alice','paper-enrich',1)
        self.assertEqual(jobs.enqueue('alice','paper-enrich',1)['id'], task['id'])
        self.assertEqual(jobs.list_jobs('alice')[0]['id'], task['id'])
        self.assertEqual(jobs.list_jobs('bob'), [])
        with self.assertRaises(HTTPException): jobs.enqueue('bob','paper-enrich',1)
        with self.assertRaises(HTTPException): asyncio.run(jobs.run(task['id'],'bob'))

    def test_claim_excludes_second_worker_and_success_atomic(self):
        task = jobs.enqueue('alice','paper-enrich',1)
        job, inputs = jobs._claim(task['id'],'alice')
        self.assertIsNone(jobs._claim(task['id'],'alice')[1])
        jobs._finish(job,'alice',inputs,{'summary_zh':'Summary','relevance':'Relevance','category':'Design'})
        self.assertEqual(self.sql('SELECT summary_zh FROM saved_papers')[0][0], 'Summary')
        self.assertEqual(jobs.list_jobs('alice'), [])
        with patch.object(jobs, '_produce', AsyncMock()) as worker:
            self.assertTrue(asyncio.run(jobs.run(task['id'],'alice'))['ok'])
        worker.assert_not_called()

    def test_interrupted_work_not_automatically_replayed_and_stale_token_rejected(self):
        task = jobs.enqueue('alice','paper-enrich',1)
        old, inputs = jobs._claim(task['id'],'alice')
        self.sql("UPDATE durable_jobs SET expires=0")
        self.assertEqual(jobs.list_jobs('alice')[0]['state'], 'interrupted')
        self.assertIsNone(jobs._claim(task['id'],'alice')[1])
        jobs.retry(task['id'],'alice')
        current, _ = jobs._claim(task['id'],'alice')
        result = jobs._finish(old,'alice',inputs,{'summary_zh':'stale'})
        self.assertEqual(result['state'], 'interrupted')
        self.assertEqual(self.sql('SELECT summary_zh FROM saved_papers')[0][0], '')
        self.assertEqual(self.sql('SELECT lease FROM durable_jobs')[0][0], current['lease'])

    def test_edited_or_deleted_paper_cannot_be_overwritten(self):
        for sql in ("UPDATE saved_papers SET summary_zh='manual'", "DELETE FROM saved_papers"):
            with self.subTest(sql=sql):
                self.sql('DELETE FROM durable_jobs')
                self.sql("INSERT OR REPLACE INTO saved_papers VALUES(1,'alice','Title','Abstract','','','')")
                task = jobs.enqueue('alice','paper-enrich',1)
                job, inputs = jobs._claim(task['id'],'alice')
                self.sql(sql)
                result = jobs._finish(job,'alice',inputs,{'summary_zh':'stale'})
                self.assertEqual(result['state'], 'cancelled')

    def test_memory_change_wins_over_old_ai_result(self):
        task = jobs.enqueue('alice','memory-core','alice')
        job, inputs = jobs._claim(task['id'],'alice')
        self.sql("UPDATE user_profiles SET updated_at='v2',memory_core='manual'")
        result = jobs._finish(job,'alice',inputs,{'memory_core':'stale'})
        self.assertEqual(result['state'],'cancelled')
        self.assertEqual(self.sql('SELECT memory_core FROM user_profiles')[0][0],'manual')

    def test_failure_sanitized_and_retry_bounded(self):
        task = jobs.enqueue('alice','paper-enrich',1)
        with patch.object(jobs, '_produce', AsyncMock(side_effect=ValueError('secret URL'))):
            for attempt in range(3):
                result = asyncio.run(jobs.run(task['id'],'alice'))
                self.assertFalse(result['ok'])
                self.assertNotIn('secret',result['task']['error'])
                if attempt < 2: jobs.retry(task['id'],'alice')
        with self.assertRaises(HTTPException): jobs.retry(task['id'],'alice')

    def test_cancellation_is_persisted(self):
        task = jobs.enqueue('alice','paper-enrich',1)
        async def exercise():
            started = asyncio.Event()
            async def wait(*_):
                started.set()
                await asyncio.Event().wait()
            with patch.object(jobs,'_produce',wait):
                running = asyncio.create_task(jobs.run(task['id'],'alice'))
                await started.wait()
                running.cancel()
                with self.assertRaises(asyncio.CancelledError): await running
        asyncio.run(exercise())
        self.assertEqual(jobs.list_jobs('alice')[0]['state'],'interrupted')

    def test_synthetic_mode_refuses_production(self):
        with patch.dict(os.environ, {'PAPERMIND_SYNTHETIC_TASKS':'true','VERCEL_ENV':'production'}):
            with self.assertRaises(RuntimeError): asyncio.run(jobs._produce({'kind':'paper-enrich'},{}))
