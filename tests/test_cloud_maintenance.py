import asyncio
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'papermind'))
import cloud_maintenance as maintenance
import cloud_state


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)/'runtime.db'
        with closing(sqlite3.connect(self.path)) as conn:
            conn.executescript(cloud_state.SCHEMA+maintenance.SCHEMA)
        for scope in (patch.object(cloud_state,'connect_remote',side_effect=lambda _:sqlite3.connect(self.path)),
                      patch.dict(os.environ,{'PAPERMIND_SQL_BACKEND':'turso','PAPERMIND_FILE_BACKEND':'vercel-blob'})):
            scope.start()
            self.addCleanup(scope.stop)

    def test_shared_lease_excludes_other_worker_and_expires(self):
        self.assertTrue(maintenance.claim())
        self.assertIsNone(maintenance.claim())
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute('UPDATE maintenance_runs SET expires=0')
            conn.commit()
        self.assertTrue(maintenance.claim())

    def test_bounded_groups_are_swept_and_receipt_persisted(self):
        with patch.object(maintenance,'candidates',return_value=[('alice','pdf'),('bob','figure')]), \
             patch.object(maintenance.cloud_files,'cleanup',AsyncMock(return_value={'reclaimed':1,'failed':0})) as pdf, \
             patch.object(maintenance.cloud_figures,'cleanup',AsyncMock(return_value={'figures_reclaimed':2,'figures_cleanup_failed':0})) as figure:
            self.assertEqual(asyncio.run(maintenance.run()),{'ok':True,'reclaimed':3,'failed':0,'groups':2})
            pdf.assert_awaited_once_with('alice',limit=3)
            figure.assert_awaited_once_with('bob',limit=3)
        self.assertEqual(maintenance.status()['status'],'done')
        self.assertTrue(maintenance.claim())

    def test_failure_never_records_success(self):
        with patch.object(maintenance,'candidates',side_effect=ValueError('private error')):
            with self.assertRaises(ValueError): asyncio.run(maintenance.run())
        self.assertEqual(maintenance.status()['status'],'failed')
        self.assertNotIn('private',str(maintenance.status()))

    def test_cron_requires_configured_secret_and_admin_requires_account(self):
        with patch.dict(os.environ,{'PAPERMIND_SQL_BACKEND':'sqlite'}):
            import api
        from fastapi.testclient import TestClient
        with patch.object(api.account_auth,'identity',return_value=None), TestClient(api.app) as client:
            with patch.dict(os.environ,{'CRON_SECRET':''}):
                self.assertEqual(client.get('/internal/maintenance/storage').status_code,503)
            with patch.dict(os.environ,{'CRON_SECRET':'synthetic-secret-'*3}),patch.object(maintenance,'run',AsyncMock(return_value={'ok':True})) as run:
                self.assertEqual(client.get('/internal/maintenance/storage').status_code,401)
                run.assert_not_awaited()
                self.assertEqual(client.get('/internal/maintenance/storage',headers={'Authorization':'Bearer '+'synthetic-secret-'*3}).status_code,200)
            self.assertEqual(client.post('/api/maintenance/storage',json={}).status_code,401)

    def test_local_mode_cannot_run_cloud_sweep(self):
        with patch.dict(os.environ,{'PAPERMIND_SQL_BACKEND':'sqlite'}):
            with self.assertRaises(HTTPException): asyncio.run(maintenance.run())
