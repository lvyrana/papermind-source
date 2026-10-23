import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "papermind"))
import api
from src import database
import llm_router
import runtime_limits as limits
import storage_limits
import trial_access
from fastapi import HTTPException
from fastapi.testclient import TestClient

UID = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / "limits"
        self.scope = patch.object(limits, "STATE_DIR", self.state)
        self.scope.start()
        self.env = patch.dict(os.environ, {"AI_MAX_CONCURRENT_REQUESTS": "1", "GLOBAL_DAILY_AI_REQUEST_LIMIT": "2"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.scope.stop()
        self.temp.cleanup()

    def count(self):
        with sqlite3.connect(self.state / "daily.sqlite3") as db:
            return db.execute("SELECT sum(count) FROM attempts").fetchone()[0]

    def test_failure_consumes_attempt_but_releases_slot(self):
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(side_effect=ValueError("fixture")))))
        with self.assertRaises(ValueError):
            limits.completion_sync(client, model="fixture")
        with limits.ai_slot():
            with self.assertRaises(limits.RuntimeLimitError):
                with limits.ai_slot():
                    pass
        with self.assertRaises(limits.RuntimeLimitError):
            with limits.ai_slot():
                pass
        self.assertEqual(self.count(), 2)
        self.assertEqual((self.state / "daily.sqlite3").stat().st_mode & 0o777, 0o600)

    def test_atomic_budget_across_processes_and_restart(self):
        code = """import sys
from pathlib import Path
import runtime_limits as limits
limits.STATE_DIR = Path(sys.argv[1])
try:
    with limits.ai_slot(): pass
except limits.RuntimeLimitError:
    sys.exit(9)
"""
        env = {**os.environ, "PYTHONPATH": str(Path(limits.__file__).parent), "AI_MAX_CONCURRENT_REQUESTS": "8", "GLOBAL_DAILY_AI_REQUEST_LIMIT": "1"}
        children = [subprocess.Popen([sys.executable, "-c", code, str(self.state)], env=env) for _ in range(6)]
        results = [child.wait(timeout=15) for child in children]
        self.assertEqual(results.count(0), 1)
        self.assertEqual(results.count(9), 5)
        self.assertEqual(self.count(), 1)

    def test_process_exit_releases_slot_without_expiry(self):
        code = """import sys, os
from pathlib import Path
import runtime_limits as limits
limits.STATE_DIR = Path(sys.argv[1])
with limits.ai_slot(): os._exit(0)
"""
        env = {**os.environ, "PYTHONPATH": str(Path(limits.__file__).parent)}
        subprocess.run([sys.executable, "-c", code, str(self.state)], env=env, check=True, timeout=15)
        with limits.ai_slot():
            pass
        self.assertEqual(self.count(), 2)

    def test_async_cancellation_releases_slot(self):
        async def exercise():
            started = asyncio.Event()
            async def wait_forever(**kwargs):
                started.set()
                await asyncio.Event().wait()
            client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=wait_forever)))
            task = asyncio.create_task(limits.completion_async(client))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            with limits.ai_slot():
                pass
        asyncio.run(exercise())
        self.assertEqual(self.count(), 2)

    def test_router_does_not_swallow_budget_error_or_retry_in_sdk(self):
        provider = {"name": "qwen", "api_key": "test-only", "base_url": "https://example.invalid", "model": "fixture"}
        client = llm_router._build_llm_client(provider)
        self.assertEqual(client.max_retries, 0)
        client.close()
        async def exercise():
            client = llm_router._build_async_llm_client(provider)
            self.assertEqual(client.max_retries, 0)
            with patch.object(llm_router, "_ordered_llm_slots", return_value=[provider]), \
                 patch.object(llm_router, "_build_async_llm_client", return_value=client), \
                 patch.object(llm_router, "completion_async", AsyncMock(side_effect=limits.RuntimeLimitError("budget"))):
                with self.assertRaises(limits.RuntimeLimitError):
                    await llm_router._llm_chat_complete_async([], task="chat")
            await client.close()
        asyncio.run(exercise())

    def test_runtime_errors_distinguish_admission_from_storage_failure(self):
        for error, expected in ((limits.RuntimeLimitError("budget"), 429),
                                (limits.RuntimeStoreUnavailable("unavailable"), 503)):
            response = asyncio.run(api.runtime_limit_response(None, error))
            self.assertEqual(response.status_code, expected)


class InvitationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "trial-access.json"
        self.scope = patch.object(trial_access, "ACCESS_FILE", self.path)
        self.scope.start()
        self.env = patch.dict(os.environ, {"PAPERMIND_INVITE_ONLY": "true"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.scope.stop()
        self.temp.cleanup()

    def grant(self):
        trial_access.update(UID, datetime.now(timezone.utc) + timedelta(days=7))

    def test_existing_uuid_grant_revoke_extend_without_plaintext_identity(self):
        self.assertFalse(trial_access.allowed(UID))
        self.grant()
        self.assertTrue(trial_access.allowed(UID))
        self.assertFalse(trial_access.allowed(OTHER))
        self.assertNotIn(UID, self.path.read_text())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        trial_access.update(UID, revoke=True)
        self.assertFalse(trial_access.allowed(UID))
        self.grant()
        self.assertTrue(trial_access.allowed(UID))
        self.assertFalse(trial_access.allowed(UID, now=datetime.now(timezone.utc) + timedelta(days=8)))

    def test_missing_malformed_expired_and_linked_stores_deny(self):
        for content in ("", "[]", "null", "{}", json.dumps({trial_access.key_for(UID): []}),
                        json.dumps({trial_access.key_for(UID): {"expires_at": "2020-01-01"}})):
            self.path.write_text(content)
            self.assertFalse(trial_access.allowed(UID))
        self.path.unlink()
        self.path.symlink_to(self.path.parent / "absent")
        self.assertFalse(trial_access.allowed(UID))

    def test_owner_exemption_and_default_off_preserve_existing_clients(self):
        self.assertTrue(trial_access.allowed(UID, owner=UID))
        self.assertFalse(trial_access.allowed(OTHER, owner=UID))
        with patch.dict(os.environ, {"PAPERMIND_INVITE_ONLY": "false"}):
            self.assertTrue(trial_access.allowed(UID))

    def test_real_api_admission_covers_previous_anonymous_endpoints(self):
        with TestClient(api.app) as client, patch.object(api, "OWNER_UID", ""):
            for path in ("/api/lookup-paper", "/api/pdf-url", "/api/export/ris-direct", "/api/export/bibtex-direct", "/api/config"):
                self.assertEqual(client.get(path).status_code, 401)
                self.assertEqual(client.get(path, headers={"X-User-ID": UID}).status_code, 403)
            self.assertEqual(client.get("/api/zotero-plugin/update.json").status_code, 200)
            self.grant()
            self.assertEqual(client.get("/api/library", headers={"X-User-ID": UID}).status_code, 200)
            client.cookies.set("papermind-uid", UID)
            self.assertEqual(client.get("/api/library").status_code, 200)
            trial_access.update(UID, revoke=True)
            self.assertEqual(client.get("/api/library").status_code, 403)


class UploadQuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"UPLOAD_TOTAL_LIMIT_MB": "2", "UPLOAD_USER_LIMIT_MB": "1"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def put(self, name, content, ids):
        return storage_limits.store_upload(self.root / name, content, ids, self.root)

    def test_per_user_total_and_replacement_accounting(self):
        size = 1024 * 1024
        self.put("pdfs/1.pdf", b"a" * size, [1])
        self.put("pdfs/1.pdf", b"b" * size, [1])
        self.put("pdfs/2.pdf", b"c" * size, [2])
        with self.assertRaises(HTTPException) as error:
            self.put("figures/1-test.png", b"x", [1])
        self.assertEqual(error.exception.status_code, 413)
        self.put("pdfs/1.pdf", b"small", [1])
        self.put("figures/1-test.png", b"x", [1])
        self.assertEqual((self.root / "pdfs/1.pdf").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / "pdfs/2.pdf").read_bytes(), b"c" * size)

    def test_own_quota_even_when_global_has_room(self):
        self.put("pdfs/1.pdf", b"x" * (1024 * 1024), [1])
        with self.assertRaises(HTTPException) as error:
            self.put("figures/1-test.png", b"y", [1])
        self.assertIn("当前设备", error.exception.detail)

    def test_simultaneous_uploads_cannot_cross_quota(self):
        def attempt(number):
            try:
                self.put(f"figures/1-{number}.png", b"x" * (700 * 1024), [1])
                return True
            except HTTPException:
                return False
        with ThreadPoolExecutor(max_workers=4) as executor:
            self.assertEqual(sum(executor.map(attempt, range(4))), 1)

    def test_outside_linked_and_unowned_destinations_rejected(self):
        for name, ids in (("outside", [1]), ("pdfs/2.pdf", [1])):
            with self.assertRaises(HTTPException):
                self.put(name, b"x", ids)
        (self.root / "outside").mkdir()
        (self.root / "pdfs").symlink_to(self.root / "outside")
        with self.assertRaises(HTTPException):
            self.put("pdfs/1.pdf", b"x", [1])

    def test_delete_during_upload_read_cannot_resurrect_pdf_or_figure(self):
        request = SimpleNamespace(headers={"X-User-ID": UID}, cookies={})
        with patch.object(database, "DB_PATH", self.root / "fixture.db"), \
             patch.object(api, "PDF_DIR", self.root / "pdfs"), \
             patch.object(api, "FIGURES_DIR", self.root / "figures"):
            database.init_db()
            for figure in (False, True):
                paper_id = database.save_paper({"title": f"Synthetic {figure}"}, UID)
                class DeleteWhileReading:
                    content_type = "application/pdf"
                    async def read(inner_self, size):
                        self.assertTrue((await api.api_delete_from_library(paper_id, request))["ok"])
                        return b"\x89PNG\r\n\x1a\nfixture" if figure else b"%PDF-1.7 fixture"
                if figure:
                    result = asyncio.run(api.api_add_board_figure(paper_id, request, DeleteWhileReading(), "methods", None, ""))
                    self.assertFalse(result["ok"])
                else:
                    with self.assertRaises(HTTPException) as error:
                        asyncio.run(api.api_upload_paper_pdf(paper_id, request, DeleteWhileReading()))
                    self.assertEqual(error.exception.status_code, 404)
            self.assertEqual(list((self.root / "pdfs").glob("*")), [])
            self.assertEqual(list((self.root / "figures").glob("*")), [])

    def test_failed_attachment_deletion_retains_database_ownership(self):
        request = SimpleNamespace(headers={"X-User-ID": UID}, cookies={})
        with patch.object(database, "DB_PATH", self.root / "fixture.db"), \
             patch.object(api, "PDF_DIR", self.root / "pdfs"), \
             patch.object(api, "FIGURES_DIR", self.root / "figures"):
            database.init_db()
            paper_id = database.save_paper({"title": "Synthetic cleanup failure"}, UID)
            with patch.object(Path, "unlink", side_effect=PermissionError("fixture")):
                with self.assertRaises(HTTPException):
                    asyncio.run(api.api_delete_from_library(paper_id, request))
            self.assertTrue(api._get_owned_paper_or_none(paper_id, UID))
