"""Migration preparation stays offline and never rewrites a live data directory."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import backup_data
import prepare_turso_snapshot as migration


class PreparationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        for target, name, value in (
            (migration.database, "DB_PATH", self.data / "paperdiary.db"),
            (migration.auth, "AUTH_DB", self.data / "auth.sqlite3"),
        ):
            replacement = patch.object(target, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        environment = patch.dict(os.environ, {"PAPERMIND_SQL_BACKEND": "sqlite"})
        environment.start()
        self.addCleanup(environment.stop)
        migration.database.init_db()
        migration.database.save_paper({"title": "Synthetic migration fixture"}, "owner-fixture")
        with migration.auth.db() as conn:
            conn.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?)",
                         ("account-fixture", "42", "Synthetic", "owner-fixture", 1))
            conn.execute("INSERT INTO sessions VALUES (?, ?, ?)", ("old-session", "account-fixture", 9999999999))
        (self.data / "pdfs").mkdir()
        (self.data / "pdfs/fixture.pdf").write_bytes(b"synthetic attachment")
        (self.data / "runtime-limits").mkdir()
        with closing(sqlite3.connect(self.data / "runtime-limits/daily.sqlite3")) as conn:
            conn.execute("CREATE TABLE attempts (day TEXT PRIMARY KEY, count INTEGER NOT NULL)")
            conn.execute("INSERT INTO attempts VALUES ('2026-10-22', 7)")
            conn.commit()
        self.snapshot = backup_data.create(self.data, self.root / "snapshots")

    def test_copy_preserves_records_budget_and_files_but_revokes_sessions(self):
        before = {name: backup_data.digest(self.snapshot / name)
                  for name in backup_data.verify(self.snapshot)["files"]}
        destination = self.root / "prepared"
        receipt = migration.prepare(self.snapshot, destination)
        self.assertFalse(receipt["uploaded"])
        self.assertTrue(receipt["sessions_revoked_in_copy"])
        for role, record in receipt["databases"].items():
            path = destination / record["file"]
            self.assertEqual(backup_data.digest(path), record["sha256"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute("SELECT version FROM _papermind_schema WHERE role=?", (role,)).fetchone()[0],
                                 migration.SCHEMA_VERSION)
                self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "delete")
                if role == "library":
                    self.assertEqual(conn.execute("SELECT user_id FROM saved_papers").fetchone()[0], "owner-fixture")
                elif role == "auth":
                    self.assertEqual(conn.execute("SELECT data_uid FROM accounts").fetchone()[0], "owner-fixture")
                    self.assertEqual(conn.execute("SELECT count(*) FROM sessions").fetchone()[0], 0)
                else:
                    self.assertEqual(conn.execute("SELECT count FROM attempts").fetchone()[0], 7)
                    self.assertEqual(conn.execute("SELECT version FROM _papermind_runtime_schema").fetchone()[0], 1)
                    self.assertEqual(conn.execute("SELECT count(*) FROM ai_leases").fetchone()[0], 0)
        self.assertEqual((destination / "pdfs/fixture.pdf").read_bytes(), b"synthetic attachment")
        receipt_path = destination / "PREPARATION-RECEIPT.json"
        self.assertEqual(json.loads(receipt_path.read_text()), receipt)
        self.assertEqual(receipt_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(before, {name: backup_data.digest(self.snapshot / name) for name in before})
        self.assertEqual(migration.database.DB_PATH, self.data / "paperdiary.db")
        self.assertEqual(migration.auth.AUTH_DB, self.data / "auth.sqlite3")
        with migration.auth.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM sessions").fetchone()[0], 1)

    def test_existing_destination_is_never_overwritten(self):
        with self.assertRaises(ValueError):
            migration.prepare(self.snapshot, self.data)
        self.assertEqual(migration.database.get_saved_papers("owner-fixture")[0]["title"], "Synthetic migration fixture")

    def test_remote_environment_rejected_before_read_or_connect(self):
        with patch.dict(os.environ, {"PAPERMIND_SQL_BACKEND": "turso"}), patch.object(migration, "verify") as verify:
            with self.assertRaises(ValueError):
                migration.prepare(self.snapshot, self.root / "prepared")
            verify.assert_not_called()

    def test_missing_account_database_blocks_preparation(self):
        with patch.object(migration, "verify", return_value={"files": {"paperdiary.db": {}}}):
            with self.assertRaisesRegex(ValueError, "ownership"):
                migration.prepare(self.snapshot, self.root / "prepared")
        self.assertFalse((self.root / "prepared").exists())

    def test_corrupt_snapshot_is_rejected_before_creating_destination(self):
        (self.snapshot / "pdfs/fixture.pdf").write_bytes(b"corrupted")
        with self.assertRaises(ValueError):
            migration.prepare(self.snapshot, self.root / "prepared")
        self.assertFalse((self.root / "prepared").exists())

    def test_failed_schema_preparation_restores_paths_without_success_receipt(self):
        with patch.object(migration.database, "init_db", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaises(RuntimeError):
                migration.prepare(self.snapshot, self.root / "prepared")
        self.assertEqual(migration.database.DB_PATH, self.data / "paperdiary.db")
        self.assertEqual(migration.auth.AUTH_DB, self.data / "auth.sqlite3")
        self.assertFalse((self.root / "prepared/PREPARATION-RECEIPT.json").exists())
