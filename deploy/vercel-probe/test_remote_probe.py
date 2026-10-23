"""Offline safeguards and transaction regression; no cloud credentials used."""
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import app
import remote_probe as probe


class RemoteProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = str(Path(self.temp.name) / "synthetic.db")

    def factory(self):
        return sqlite3.connect(self.database)

    def test_empty_initialization_is_repeatable_and_checks_pass(self):
        for _ in range(2):
            self.assertTrue(probe.initialize_empty_database(self.factory)["initialized"])
        first = probe.run_checks(self.factory)
        second = probe.run_checks(self.factory)
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(len(first["checks"]), 6)
        with self.factory() as conn:
            self.assertEqual(conn.execute(f"SELECT count(*) FROM {probe.ROWS}").fetchone()[0], 4)

    def test_initialization_refuses_existing_product_database(self):
        with self.factory() as conn:
            conn.execute("CREATE TABLE papers (title TEXT)")
            conn.execute("INSERT INTO papers VALUES ('existing data')")
        with self.assertRaisesRegex(probe.ProbeFailure, "refusing_nonempty"):
            probe.initialize_empty_database(self.factory)
        with self.factory() as conn:
            self.assertEqual(conn.execute("SELECT title FROM papers").fetchone(), ("existing data",))
            self.assertEqual(conn.execute("SELECT count(*) FROM sqlite_master WHERE name=?", (probe.ROWS,)).fetchone()[0], 0)

    def test_missing_or_wrong_marker_cannot_write(self):
        with self.assertRaisesRegex(probe.ProbeFailure, "marker"):
            probe.run_checks(self.factory)
        probe.initialize_empty_database(self.factory)
        with self.factory() as conn:
            conn.execute(f"UPDATE {probe.MARKER} SET purpose='not-a-test-database'")
        with self.assertRaisesRegex(probe.ProbeFailure, "marker"):
            probe.run_checks(self.factory)
        with self.factory() as conn:
            self.assertEqual(conn.execute(f"SELECT count(*) FROM {probe.ROWS}").fetchone()[0], 0)

    def test_configuration_rejects_local_or_untrusted_urls(self):
        for url in ("file:///tmp/a", "http://example.test", "https://host.invalid", "libsql://secret@db.turso.io"):
            with patch.dict(os.environ, {"TURSO_DATABASE_URL": url, "TURSO_AUTH_TOKEN": "not-a-real-token"}):
                with self.assertRaises(probe.ProbeFailure):
                    probe.connect()

    def test_errors_do_not_disclose_driver_details(self):
        def fail():
            raise RuntimeError("url-with-private-token")
        with self.assertRaises(probe.ProbeFailure) as result:
            probe.run_checks(fail)
        self.assertEqual(str(result.exception), "connect")

    def test_http_gates_never_connect_without_preview_and_header(self):
        with TestClient(app.app) as client, patch.object(app, "run_checks") as run:
            for environment, headers in (("production", {"X-Litfinch-Probe": "synthetic-v1"}),
                                         ("preview", {})):
                with patch.dict(os.environ, {"VERCEL_ENV": environment}):
                    self.assertEqual(client.post("/checks/database", headers=headers).status_code, 403)
            self.assertEqual(client.get("/checks/database").status_code, 405)
            run.assert_not_called()

    def test_http_success_and_sanitized_failure(self):
        headers = {"X-Litfinch-Probe": "synthetic-v1"}
        with TestClient(app.app) as client, patch.dict(os.environ, {"VERCEL_ENV": "preview"}):
            with patch.object(app, "run_checks", return_value={"ok": True}):
                self.assertEqual(client.post("/checks/database", headers=headers).json()["ok"], True)
            with patch.object(app, "run_checks", side_effect=probe.ProbeFailure("commit")):
                response = client.post("/checks/database", headers=headers)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"ok": False, "stage": "commit"})

    def test_upload_allowlist_excludes_env_and_test_files(self):
        allowlist = (Path(__file__).parent / ".vercelignore").read_text().splitlines()
        self.assertEqual(allowlist, ["*", "!app.py", "!remote_probe.py", "!.python-version", "!vercel.json", "!requirements.txt"])


if __name__ == "__main__":
    unittest.main()
