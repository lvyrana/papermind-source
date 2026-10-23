"""Schema preparation uses fresh files and never overwrites existing databases."""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import initialize_turso_preview as preview


class PreviewInitializationTests(unittest.TestCase):
    def test_business_schema_and_repeat_initialization(self):
        for role, (script, fingerprint, count) in preview.schema_scripts().items():
            with self.subTest(role=role), closing(sqlite3.connect(":memory:")) as conn:
                self.assertGreater(count, 0)
                self.assertEqual(preview.initialize(conn, role, script, fingerprint), "initialized")
                self.assertEqual(preview.initialize(conn, role, script, fingerprint), "already_prepared")
                if role == "library":
                    self.assertEqual(conn.execute("SELECT count(*) FROM saved_papers").fetchone()[0], 0)
                    conn.execute("SELECT has_pdf, last_exported_at FROM saved_papers")

    def test_existing_data_and_wrong_role_are_refused(self):
        script, fingerprint, _ = preview.schema_scripts()["auth"]
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.execute("CREATE TABLE existing (value TEXT)")
            conn.execute("INSERT INTO existing VALUES ('keep')")
            conn.commit()
            with self.assertRaises(RuntimeError):
                preview.initialize(conn, "auth", script, fingerprint)
            self.assertEqual(conn.execute("SELECT value FROM existing").fetchone()[0], "keep")
        with closing(sqlite3.connect(":memory:")) as conn:
            preview.initialize(conn, "auth", script, fingerprint)
            with self.assertRaises(RuntimeError):
                preview.initialize(conn, "library", script, fingerprint)

    def test_failed_schema_rolls_back_without_completion_marker(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            with self.assertRaises(RuntimeError):
                preview.initialize(conn, "auth", "CREATE TABLE first (id INTEGER); INVALID SQL;", "bad")
            self.assertEqual(conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0], 0)
