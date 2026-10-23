from contextlib import closing
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'papermind'))
import cloud_state as state
import trial_access
from src import config_store
from runtime_limits import RuntimeStoreUnavailable


class SharedStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'runtime.db'
        with closing(sqlite3.connect(self.path)) as conn:
            conn.executescript(state.SCHEMA)
        for scope in (patch.dict(os.environ, {'PAPERMIND_SQL_BACKEND':'turso','PAPERMIND_INVITE_ONLY':'true'}),
                      patch.object(state, 'connect_remote', side_effect=lambda _: sqlite3.connect(self.path))):
            scope.start()
            self.addCleanup(scope.stop)

    def test_config_persists_across_connections_without_disk_fallback(self):
        config_store.save_api_settings({'model':'synthetic', 'unrelated':'keep'})
        config_store.save_custom_provider({'enabled':False,'api_key':'synthetic-secret-not-real','model':'mock'})
        self.assertEqual(config_store.get_custom_provider()['api_key'],'synthetic-secret-not-real')
        self.assertEqual(config_store.get_api_settings()['unrelated'],'keep')
        self.assertNotIn('api_key', config_store.get_custom_provider_safe())
        with patch.object(state, 'connect_remote', side_effect=ValueError('secret credentials')):
            with self.assertRaisesRegex(RuntimeStoreUnavailable,'云端配置服务暂不可用') as error:
                config_store.get_custom_provider()
            self.assertNotIn('secret',str(error.exception))

    def test_invitation_expiry_revocation_and_validation(self):
        uid = str(uuid.uuid4())
        self.assertFalse(trial_access.allowed(uid))
        trial_access.update(uid, datetime.now(timezone.utc)+timedelta(hours=1))
        self.assertTrue(trial_access.allowed(uid))
        self.assertFalse(trial_access.allowed(str(uuid.uuid4())))
        trial_access.update(uid,revoke=True)
        self.assertFalse(trial_access.allowed(uid))
        with self.assertRaises(ValueError): trial_access.update(uid,datetime.now())
        self.assertFalse(trial_access.allowed('invalid'))
        trial_access.update(uid,datetime.now(timezone.utc)+timedelta(hours=1))
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute('UPDATE trial_invitations SET expires=0')
            conn.commit()
        self.assertFalse(trial_access.allowed(uid))

    def test_cooldown_shared_and_rotation_does_not_inherit_failure(self):
        provider = {'name':'test','model':'mock','base_url':'https://invalid.example','api_key':'synthetic'}
        self.assertFalse(state.provider_cooled(provider))
        state.cooldown_provider(provider, 600)
        self.assertTrue(state.provider_cooled(provider))
        self.assertFalse(state.provider_cooled({**provider,'api_key':'rotated'}))
        with closing(sqlite3.connect(self.path)) as conn:
            rows = conn.execute('SELECT * FROM provider_cooldowns').fetchall()
            self.assertEqual(len(rows[0][0]),64)
            conn.execute('UPDATE provider_cooldowns SET expires=0')
            conn.commit()
        self.assertFalse(state.provider_cooled(provider))

    def test_unavailable_config_does_not_enable_builtin_fallback(self):
        import llm_router
        with patch.object(state, 'connect_remote', side_effect=ValueError('private URL')):
            with self.assertRaises(RuntimeStoreUnavailable): llm_router._get_custom_slots()

    def test_initializer_is_guarded_and_idempotent(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'scripts'))
        from initialize_preview_state import migrate
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.execute('CREATE TABLE _papermind_preview_init(purpose TEXT,role TEXT)')
            conn.execute("INSERT INTO _papermind_preview_init VALUES('wrong','runtime')")
            with self.assertRaises(ValueError): migrate(conn)
            conn.execute("UPDATE _papermind_preview_init SET purpose='litfinch-synthetic-business-v1'")
            self.assertEqual(migrate(conn),'initialized')
            self.assertEqual(migrate(conn),'already_prepared')
