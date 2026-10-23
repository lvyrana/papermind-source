"""libSQL compatibility tests use local synthetic files, never a remote account."""
from contextlib import closing
import importlib.util
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from fastapi import HTTPException
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "papermind"))
import sql_backend
from src import database
import auth


class ConfigurationTests(unittest.TestCase):
    def test_default_stays_sqlite_and_unknown_backend_fails(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(sql_backend.remote_enabled())
        with patch.dict(os.environ, {"PAPERMIND_SQL_BACKEND": "typo"}):
            with self.assertRaises(RuntimeError):
                sql_backend.remote_enabled()

    def test_missing_credentials_or_unsafe_url_never_fall_back(self):
        for url in ("", "file:///tmp/data.db", "http://example.test", "https://user:secret@example.test",
                    "https://example.test?secret=private", "https://example.test:invalid", "https://[broken"):
            with self.subTest(url=url), patch.dict(os.environ, {"PAPERMIND_LIBRARY_DATABASE_URL": url,
                                                              "PAPERMIND_LIBRARY_AUTH_TOKEN": "synthetic"}):
                with self.assertRaises(RuntimeError) as raised:
                    sql_backend.connect_remote("library")
                self.assertNotIn("secret=private", str(raised.exception))

    def test_unknown_role_rejected(self):
        with self.assertRaises(ValueError):
            sql_backend.connect_remote("../auth")

    def test_native_marketplace_pair_and_matching_legacy_pair(self):
        values = {"PAPERMIND_LIBRARY_TURSO_DATABASE_URL": "libsql://library.turso.io",
                  "PAPERMIND_LIBRARY_TURSO_AUTH_TOKEN": "synthetic"}
        with patch.dict(os.environ, values, clear=True):
            self.assertEqual(sql_backend.remote_credentials("library"), ("libsql://library.turso.io", "synthetic"))
        values.update({"PAPERMIND_LIBRARY_DATABASE_URL": "libsql://library.turso.io",
                       "PAPERMIND_LIBRARY_AUTH_TOKEN": "synthetic"})
        with patch.dict(os.environ, values, clear=True):
            self.assertEqual(sql_backend.remote_credentials("library"), ("libsql://library.turso.io", "synthetic"))

    def test_conflicting_or_mixed_pairs_fail_without_disclosing_values(self):
        for values in (
            {"PAPERMIND_LIBRARY_DATABASE_URL": "libsql://old.turso.io",
             "PAPERMIND_LIBRARY_AUTH_TOKEN": "private-old",
             "PAPERMIND_LIBRARY_TURSO_DATABASE_URL": "libsql://new.turso.io",
             "PAPERMIND_LIBRARY_TURSO_AUTH_TOKEN": "private-new"},
            {"PAPERMIND_LIBRARY_DATABASE_URL": "libsql://old.turso.io",
             "PAPERMIND_LIBRARY_TURSO_AUTH_TOKEN": "private-new"},
        ):
            with patch.dict(os.environ, values, clear=True):
                with self.assertRaises(RuntimeError) as error:
                    sql_backend.remote_credentials("library")
                self.assertNotIn("private-", str(error.exception))
                self.assertNotIn("turso.io", str(error.exception))

    def test_row_contract(self):
        row = sql_backend.Row((("id",), ("Title",)), (42, "合成论文"))
        self.assertEqual(row[0], 42)
        self.assertEqual(row["title"], "合成论文")
        self.assertEqual(dict(row), {"id": 42, "Title": "合成论文"})
        self.assertEqual(tuple(row), (42, "合成论文"))

    def test_remote_read_does_not_acquire_or_finish_a_write_transaction(self):
        conn = Mock()
        with patch.object(auth, "remote_enabled", return_value=True), patch.object(auth, "connect_remote", return_value=conn):
            with auth.db(readonly=True) as reader:
                reader.execute("SELECT 1")
        conn.execute.assert_called_once_with("SELECT 1")
        conn.commit.assert_not_called()
        conn.rollback.assert_not_called()
        conn.close.assert_called_once()

    def test_expired_transaction_rollback_keeps_original_error(self):
        conn = Mock()
        conn.rollback.side_effect = ValueError("no transaction is active")
        with patch.object(auth, "remote_enabled", return_value=True), patch.object(auth, "connect_remote", return_value=conn):
            with self.assertRaisesRegex(RuntimeError, "original failure"), auth.db():
                raise RuntimeError("original failure")
        conn.close.assert_called_once()


@unittest.skipUnless(importlib.util.find_spec("libsql"), "Install deploy/requirements-turso.txt for the driver matrix")
class DriverTests(unittest.TestCase):
    def setUp(self):
        import libsql
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        for target, key, value in ((database, "DB_PATH", self.path / "library.db"),
                                   (auth, "AUTH_DB", self.path / "auth.db")):
            p = patch.object(target, key, value)
            p.start(); self.addCleanup(p.stop)
        with patch.dict(os.environ, {"PAPERMIND_SQL_BACKEND": "sqlite"}):
            database.init_db()
            with auth.db():
                pass
        for role in ("library", "auth"):
            with closing(sqlite3.connect(self.path / (role + ".db"))) as conn:
                sql_backend.stamp_schema(conn, role)
        raw_connect = libsql.connect
        self.calls = []
        def connect(url, **kwargs):
            self.calls.append((url, kwargs))
            role = url.split("//")[1].split(".")[0]
            return raw_connect(str(self.path / (role + ".db")))
        p = patch.object(libsql, "connect", side_effect=connect)
        p.start(); self.addCleanup(p.stop)
        values = {"PAPERMIND_SQL_BACKEND": "turso"}
        for role in ("LIBRARY", "AUTH"):
            values["PAPERMIND_" + role + "_DATABASE_URL"] = "libsql://" + role.lower() + ".example.test"
            values["PAPERMIND_" + role + "_AUTH_TOKEN"] = "synthetic"
        p = patch.dict(os.environ, values)
        p.start(); self.addCleanup(p.stop)

    def test_library_reads_writes_identity_and_restart(self):
        first = database.save_paper({"title": "合成论文", "doi": "10.test/first"}, "reader-a")
        second = database.save_paper({"title": "Other paper", "doi": "10.test/second"}, "reader-b")
        self.assertNotEqual(first, second)
        self.assertEqual([p["id"] for p in database.get_saved_papers("reader-a")], [first])
        self.assertEqual(database.get_saved_paper(first)["title"], "合成论文")
        database.init_db()  # A new connection, no ephemeral-file copy or migrations.
        self.assertEqual(database.get_saved_paper(first)["user_id"], "reader-a")
        for _, kwargs in self.calls:
            self.assertNotIn("sync_url", kwargs)

    def test_auth_transaction_rollback_and_separate_role(self):
        with self.assertRaises(ValueError), auth.db() as conn:
            conn.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?)", ("a", "42", "Synthetic", "uid", 1))
            raise ValueError("rollback")
        with auth.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM accounts").fetchone()[0], 0)
            conn.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?)", ("a", "42", "Synthetic", "uid", 1))
        with auth.db() as conn:
            self.assertEqual(dict(conn.execute("SELECT * FROM accounts").fetchone())["data_uid"], "uid")

    def test_remote_session_claim_and_logout_do_not_require_a_local_auth_file(self):
        with auth.db() as conn:
            conn.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?)", ("a", "42", "Synthetic", "uid", 1))
            token = auth.new_session(conn, "a")
        def request(method="GET"):
            return Request({"type": "http", "method": method, "headers": [
                (b"authorization", ("Bearer " + token).encode())]})
        missing = self.path / "must-not-exist" / "auth.sqlite3"
        with patch.object(auth, "AUTH_DB", missing):
            self.assertEqual(auth.identity(request())["id"], "a")
            self.assertTrue(auth.namespace_claimed("uid"))
            self.assertEqual(auth.logout(request("POST")).status_code, 200)
            with self.assertRaises(HTTPException) as error:
                auth.identity(request())
            self.assertEqual(error.exception.status_code, 401)
            self.assertFalse(missing.exists())

    def test_missing_or_wrong_schema_is_rejected_without_creation(self):
        with closing(sqlite3.connect(self.path / "library.db")) as conn:
            conn.execute("DELETE FROM _papermind_schema")
            conn.commit()
        with self.assertRaisesRegex(RuntimeError, "Prepare and verify"):
            database.get_saved_papers("reader-a")

    def test_cloud_failure_does_not_expose_token_or_use_local_data(self):
        import libsql
        with patch.object(libsql, "connect", side_effect=ValueError("sensitive-token")):
            with self.assertRaises(RuntimeError) as raised:
                database.get_saved_papers("reader-a")
            self.assertNotIn("sensitive-token", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
