from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import backup_data


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.db = sqlite3.connect(self.data / "paperdiary.db")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE example (value TEXT)")
        self.db.execute("INSERT INTO example VALUES ('synthetic')")
        self.db.commit()
        for folder in ("pdfs", "figures"):
            (self.data / folder).mkdir()
            (self.data / folder / "fixture.bin").write_bytes(b"fixture")
        (self.data / "config.json").write_text('{"test_secret":"excluded"}')
        (self.data / ".env").write_text("FAKE=excluded")
        self.backups = self.root / "backup path with spaces"

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_wal_snapshot_and_restore_round_trip(self):
        snapshot = backup_data.create(self.data, self.backups)
        manifest = backup_data.verify(snapshot)
        self.assertEqual(len(manifest["files"]), 3)
        restored = backup_data.restore(snapshot, self.root / "restored")
        with sqlite3.connect(restored / "paperdiary.db") as db:
            self.assertEqual(db.execute("SELECT value FROM example").fetchone()[0], "synthetic")
        self.assertEqual((restored / "pdfs/fixture.bin").read_bytes(), b"fixture")
        self.assertFalse((restored / "config.json").exists())
        self.assertFalse((restored / "paperdiary.db-wal").exists())
        self.assertEqual(self.backups.stat().st_mode & 0o777, 0o700)
        self.assertEqual((snapshot / "paperdiary.db").stat().st_mode & 0o777, 0o600)
        self.assertNotEqual(snapshot, backup_data.create(self.data, self.backups))

    def test_restore_never_overwrites_existing_destination(self):
        snapshot = backup_data.create(self.data, self.backups)
        with self.assertRaises(ValueError):
            backup_data.restore(snapshot, self.data)
        self.assertEqual(self.db.execute("SELECT value FROM example").fetchone()[0], "synthetic")

    def test_budget_preserved_but_old_invitation_grants_not_replayed(self):
        (self.data / "runtime-limits").mkdir()
        with sqlite3.connect(self.data / "runtime-limits/daily.sqlite3") as counter:
            counter.execute("CREATE TABLE attempts (day TEXT PRIMARY KEY, count INTEGER)")
            counter.execute("INSERT INTO attempts VALUES ('2026-09-28', 7)")
        (self.data / "trial-access.json").write_text('{"old-grant":{}}')
        snapshot = backup_data.create(self.data, self.backups)
        restored = backup_data.restore(snapshot, self.root / "restored")
        with sqlite3.connect(restored / "runtime-limits/daily.sqlite3") as counter:
            self.assertEqual(counter.execute("SELECT count FROM attempts").fetchone()[0], 7)
        self.assertFalse((restored / "trial-access.json").exists())

    def test_broad_backup_destination_rejected_before_chmod(self):
        with self.assertRaises(ValueError):
            backup_data.create(self.data, self.root)

    def test_account_ownership_restored_without_resurrecting_sessions(self):
        with sqlite3.connect(self.data / "auth.sqlite3") as auth_db:
            auth_db.execute("CREATE TABLE accounts (id TEXT)")
            auth_db.execute("INSERT INTO accounts VALUES ('owner-fixture')")
            for table in ("sessions", "flows", "devices", "attempts"):
                auth_db.execute(f"CREATE TABLE {table} (value TEXT)")
                auth_db.execute(f"INSERT INTO {table} VALUES ('expired-fixture')")
        snapshot = backup_data.create(self.data, self.backups)
        self.assertIn("auth.sqlite3", backup_data.verify(snapshot)["files"])
        restored = backup_data.restore(snapshot, self.root / "restored")
        with sqlite3.connect(restored / "auth.sqlite3") as auth_db:
            self.assertEqual(auth_db.execute("SELECT id FROM accounts").fetchone()[0], "owner-fixture")
            for table in ("sessions", "flows", "devices", "attempts"):
                self.assertEqual(auth_db.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
        self.assertEqual((restored / "auth.sqlite3").stat().st_mode & 0o777, 0o600)

    def test_low_disk_space_refuses_copy_without_deleting_old_backups(self):
        snapshot = backup_data.create(self.data, self.backups)
        with patch.object(backup_data.shutil, "disk_usage", return_value=SimpleNamespace(free=0)):
            with self.assertRaises(RuntimeError):
                backup_data.create(self.data, self.backups)
        backup_data.verify(snapshot)
        self.assertEqual([p.resolve() for p in self.backups.iterdir()], [snapshot])

    def test_corruption_unexpected_file_and_traversal_fail(self):
        snapshot = backup_data.create(self.data, self.backups)
        (snapshot / "pdfs/fixture.bin").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            backup_data.verify(snapshot)
        (snapshot / "pdfs/fixture.bin").write_bytes(b"fixture")
        (snapshot / "extra").write_text("unexpected")
        with self.assertRaises(ValueError):
            backup_data.verify(snapshot)
        for name in ("../paperdiary.db", "/paperdiary.db", "pdfs/../../outside", "pdfs\\outside"):
            self.assertFalse(backup_data.safe_name(name))

    def test_symlink_and_copy_failure_do_not_publish_partial_backups(self):
        (self.data / "pdfs/link").symlink_to(self.data / "config.json")
        with self.assertRaises(ValueError):
            backup_data.create(self.data, self.backups)
        (self.data / "pdfs/link").unlink()
        with patch.object(backup_data.shutil, "copyfile", side_effect=OSError("fixture failure")):
            with self.assertRaises(OSError):
                backup_data.create(self.data, self.backups)
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_database_only_mode_and_live_change_fail_closed(self):
        snapshot = backup_data.create(self.data, self.backups, include_files=False)
        self.assertEqual(set(backup_data.verify(snapshot)["files"]), {"paperdiary.db"})
        inventory = backup_data.file_inventory(self.data)
        with patch.object(backup_data, "file_inventory", side_effect=[inventory, {}]):
            with self.assertRaises(RuntimeError):
                backup_data.create(self.data, self.backups)
        self.assertEqual([p.resolve() for p in self.backups.iterdir()], [snapshot])
