import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "papermind"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import runtime_limits as limits
from initialize_preview_runtime import migrate


def client(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


class CloudRuntimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "shared.db"
        with closing(sqlite3.connect(self.path)) as conn:
            conn.executescript(limits.REMOTE_SCHEMA)
        for scope in (patch.dict(os.environ, {"PAPERMIND_SQL_BACKEND": "turso"}),
                      patch.object(limits, "connect_remote", side_effect=self.connect),
                      patch.object(limits, "STATE_DIR", Path(temp.name) / "must-not-exist")):
            scope.start()
            self.addCleanup(scope.stop)

    def connect(self, role):
        self.assertEqual(role, "runtime")
        return sqlite3.connect(self.path, timeout=5)

    def sql(self, query, parameters=()):
        with closing(self.connect("runtime")) as conn:
            result = conn.execute(query, parameters).fetchall()
            conn.commit()
            return result

    def count(self):
        return self.sql("SELECT COALESCE(SUM(count),0) FROM attempts")[0][0]

    def test_atomic_budget_under_contention_and_shared_policy(self):
        self.sql("UPDATE ai_policy SET daily_limit=1,concurrent_limit=8")
        def attempt(_):
            try:
                lease = limits._claim_remote()
                limits._release_remote(lease)
                return True
            except limits.RuntimeLimitError:
                return False
        with patch.dict(os.environ, {"GLOBAL_DAILY_AI_REQUEST_LIMIT": "9999"}), ThreadPoolExecutor(6) as pool:
            self.assertEqual(sum(pool.map(attempt, range(6))), 1)
        self.assertEqual(self.count(), 1)
        self.assertFalse(limits.STATE_DIR.exists())

    def test_concurrency_rejection_does_not_consume_budget(self):
        self.sql("UPDATE ai_policy SET concurrent_limit=1")
        lease = limits._claim_remote()
        with self.assertRaises(limits.RuntimeLimitError) as caught:
            limits._claim_remote()
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(self.count(), 1)
        limits._release_remote("wrong-id")
        self.assertEqual(len(self.sql("SELECT * FROM ai_leases")), 1)
        limits._release_remote(lease)
        next_lease = limits._claim_remote()
        limits._release_remote(next_lease)
        self.assertEqual(self.count(), 2)

    def test_failure_counts_and_release_is_idempotent(self):
        create = AsyncMock(side_effect=ValueError("synthetic-provider"))
        with self.assertRaisesRegex(ValueError, "synthetic-provider"):
            asyncio.run(limits.completion_async(client(create)))
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.sql("SELECT * FROM ai_leases"), [])
        self.assertEqual(create.await_count, 1)

    def test_async_cancellation_and_timeout_release(self):
        async def exercise():
            started = asyncio.Event()
            async def wait(**_):
                started.set()
                await asyncio.Event().wait()
            task = asyncio.create_task(limits.completion_async(client(wait)))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            with patch.object(limits, "REMOTE_CALL_SECONDS", 0.02):
                with self.assertRaises(limits.RuntimeStoreUnavailable):
                    await limits.completion_async(client(wait))
        asyncio.run(exercise())
        self.assertEqual(self.count(), 2)
        self.assertEqual(self.sql("SELECT * FROM ai_leases"), [])

    def test_orphan_expires_using_database_time_without_refund(self):
        self.sql("UPDATE ai_policy SET concurrent_limit=1")
        old = limits._claim_remote()
        self.sql("UPDATE ai_leases SET expires=CAST(strftime('%s','now') AS INTEGER)-1 WHERE id=?", (old,))
        new = limits._claim_remote()
        limits._release_remote(old)
        self.assertEqual(self.sql("SELECT id FROM ai_leases"), [(new,)])
        self.assertEqual(self.count(), 2)
        limits._release_remote(new)

    def test_utc_day_comes_from_database_and_old_budget_retained(self):
        self.sql("INSERT INTO attempts VALUES('2000-01-01',500)")
        with patch.object(limits, "datetime") as local_clock:
            lease = limits._claim_remote()
        local_clock.now.assert_not_called()
        self.assertEqual(self.sql("SELECT count FROM attempts WHERE day=date('now')"), [(1,)])
        self.assertEqual(self.sql("SELECT count FROM attempts WHERE day='2000-01-01'"), [(500,)])
        limits._release_remote(lease)

    def test_missing_schema_or_backend_fails_closed_without_leaking(self):
        for failure in ("network-secret-token", "database-secret-url"):
            create = AsyncMock()
            with patch.object(limits, "connect_remote", side_effect=RuntimeError(failure)):
                with self.assertRaises(limits.RuntimeStoreUnavailable) as caught:
                    asyncio.run(limits.completion_async(client(create)))
            self.assertEqual(caught.exception.status_code, 503)
            self.assertNotIn(failure, str(caught.exception))
            create.assert_not_called()
        self.sql("DROP TABLE _papermind_runtime_schema")
        with self.assertRaises(limits.RuntimeStoreUnavailable):
            limits._claim_remote()
        self.assertEqual(self.count(), 0)
        self.assertFalse(limits.STATE_DIR.exists())

    def test_release_failure_keeps_lease_and_original_exception(self):
        async def fail(**_):
            with patch.object(limits, "connect_remote", side_effect=RuntimeError("secret")):
                limits._release_remote("unrelated")
            raise ValueError("provider")
        original_release = limits._release_remote
        def broken_release(lease):
            with patch.object(limits, "connect_remote", side_effect=RuntimeError("secret")):
                original_release(lease)
        with patch.object(limits, "_release_remote", side_effect=broken_release):
            with self.assertRaisesRegex(ValueError, "provider"):
                asyncio.run(limits.completion_async(client(fail)))
        self.assertEqual(len(self.sql("SELECT * FROM ai_leases")), 1)
        self.assertEqual(self.count(), 1)

    def test_ambiguous_commit_never_retries_or_starts_provider(self):
        real = self.connect("runtime")
        class UncertainCommit:
            execute = real.execute
            rollback = real.rollback
            close = real.close
            def commit(self):
                real.commit()
                raise RuntimeError("secret-url: acknowledgement lost")
        create = AsyncMock()
        with patch.object(limits, "connect_remote", return_value=UncertainCommit()) as connect:
            with self.assertRaises(limits.RuntimeStoreUnavailable):
                asyncio.run(limits.completion_async(client(create)))
        create.assert_not_called()
        self.assertEqual(connect.call_count, 1)
        self.assertEqual(self.count(), 1)
        self.assertEqual(len(self.sql("SELECT * FROM ai_leases")), 1)

    def test_sync_stream_and_unbounded_context_are_rejected(self):
        create = Mock()
        with self.assertRaises(limits.RuntimeStoreUnavailable):
            limits.completion_sync(client(create))
        with self.assertRaises(limits.RuntimeStoreUnavailable):
            asyncio.run(limits.completion_async(client(create), stream=True))
        with self.assertRaises(limits.RuntimeStoreUnavailable):
            with limits.ai_slot():
                pass
        create.assert_not_called()
        self.assertEqual(self.count(), 0)

    def test_slow_commit_does_not_start_provider_or_refund(self):
        create = AsyncMock()
        with patch.object(limits.time, "monotonic", side_effect=[0,100]):
            with self.assertRaises(limits.RuntimeStoreUnavailable):
                limits._claim_remote()
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.sql("SELECT * FROM ai_leases"), [])
        create.assert_not_called()


class RuntimeSchemaTests(unittest.TestCase):
    def test_additive_upgrade_preserves_counts_and_repeats_safely(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.executescript("CREATE TABLE _papermind_preview_init(purpose TEXT,role TEXT);"
                "INSERT INTO _papermind_preview_init VALUES('litfinch-synthetic-business-v1','runtime');"
                "CREATE TABLE attempts(day TEXT PRIMARY KEY,count INTEGER NOT NULL);"
                "INSERT INTO attempts VALUES('2026-10-22',7);")
            self.assertEqual(migrate(conn), "initialized")
            self.assertEqual(migrate(conn), "already_prepared")
            self.assertEqual(conn.execute("SELECT count FROM attempts").fetchall(), [(7,)])

    def test_wrong_marker_refused(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.executescript("CREATE TABLE _papermind_preview_init(purpose TEXT,role TEXT);"
                              "INSERT INTO _papermind_preview_init VALUES('real','runtime');")
            with self.assertRaises(ValueError):
                migrate(conn)
            self.assertFalse(conn.execute("SELECT name FROM sqlite_master WHERE name='ai_leases'").fetchone())

    def test_lease_exceeds_function_duration(self):
        root = Path(__file__).resolve().parents[1]
        # The isolated source test launcher does not copy deployment assets.
        config = root / "deploy/vercel-preview/vercel.json"
        if not config.exists():
            self.skipTest("Deployment contract checked in source workspace")
        maximum = json.loads(config.read_text())["functions"]["app.py"]["maxDuration"]
        self.assertLess(maximum + 30, limits.REMOTE_LEASE_SECONDS)
        self.assertLess(limits.REMOTE_CALL_SECONDS + 10, maximum)
