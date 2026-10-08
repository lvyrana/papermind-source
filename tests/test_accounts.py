"""Run via scripts/test_backend_isolated.py; OAuth provider is synthetic."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "papermind"))
import api
import auth
from src import database

UID = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
BASE = "https://papermind.test"


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for target, attr, value in [(auth, "AUTH_DB", Path(self.temp.name) / "auth.sqlite3"),
                                    (database, "DB_PATH", Path(self.temp.name) / "papers.db")]:
            p = patch.object(target, attr, value); p.start(); self.addCleanup(p.stop)
        env = patch.dict(os.environ, {"PAPERMIND_PUBLIC_ORIGIN": BASE,
            "ZOTERO_OAUTH_CLIENT_KEY": "test-client", "ZOTERO_OAUTH_CLIENT_SECRET": "test-secret",
            "PAPERMIND_INVITE_ONLY": "false", "PAPERMIND_REQUIRE_AI_LOGIN": "true"})
        env.start(); self.addCleanup(env.stop)
        self.client = TestClient(api.app, base_url=BASE)
        self.addCleanup(self.client.close)
        self.headers = {"Origin": BASE, "X-User-ID": UID}
        self.serial = 0

    def start(self, body=None, client=None, headers=None):
        self.serial += 1
        token = "request-" + str(self.serial)
        with patch.object(auth, "exchange", AsyncMock(return_value={"oauth_token": token,
             "oauth_token_secret": "temporary-secret", "oauth_callback_confirmed": "true"})):
            response = (client or self.client).post("/api/auth/zotero/start",
                         json=body or {"adopt_legacy": True}, headers=headers or self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        url = response.json()["authorize_url"]
        self.assertEqual(parse_qs(urlsplit(url).query)["identity"], ["1"])
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        return token

    def complete(self, token, zotero_id="42", client=None):
        with patch.object(auth, "exchange", AsyncMock(return_value={"userID": zotero_id, "username": "Synthetic reader",
             "oauth_token": "must-not-retain", "oauth_token_secret": "must-not-retain"})):
            return (client or self.client).get("/api/auth/zotero/callback", params={"oauth_token": token, "oauth_verifier": "verified"}, follow_redirects=False)

    def test_web_login_keeps_old_records_and_rejects_legacy_bypass(self):
        paper_id = database.save_paper({"title": "Original paper", "pmid": "123"}, UID)
        response = self.complete(self.start())
        self.assertEqual(response.status_code, 303, response.text)
        self.assertIn("Secure", response.headers["set-cookie"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        status = self.client.get("/api/auth/status").json()
        self.assertEqual(status["account"]["data_uid"], UID)
        # Cookie wins even if a stale or hostile UID header names another user.
        self.assertEqual(api._get_user_id(type('R', (), {'headers': {'X-User-ID': OTHER}, 'cookies': dict(self.client.cookies)})()), UID)
        self.assertEqual(database.get_saved_paper(paper_id)["title"], "Original paper")
        anonymous = TestClient(api.app, base_url=BASE)
        self.assertEqual(anonymous.get("/api/profile", headers={"X-User-ID": UID}).status_code, 401)
        raw = auth.AUTH_DB.read_bytes()
        self.assertNotIn(b"must-not-retain", raw)
        self.assertNotIn(self.client.cookies.get(auth.SESSION_COOKIE).encode(), raw)
        self.assertEqual(auth.AUTH_DB.stat().st_mode & 0o777, 0o600)

    def test_cookie_csrf_and_logout_revocation(self):
        self.complete(self.start())
        old = self.client.cookies.get(auth.SESSION_COOKIE)
        self.assertEqual(self.client.post("/api/profile", json={}, headers={"Origin": "https://evil.test"}).status_code, 403)
        self.assertEqual(self.client.post("/api/auth/logout", json={}, headers={"Origin": "https://evil.test"}).status_code, 403)
        self.assertEqual(self.client.post("/api/auth/logout", json={}, headers={"Origin": BASE}).status_code, 200)
        self.assertEqual(self.client.get("/api/auth/status").json()["authenticated"], False)
        self.assertEqual(self.client.get("/api/profile", headers={"Authorization": "Bearer " + old, "X-User-ID": OTHER}).status_code, 401)

    def test_callback_browser_binding_expiry_and_replay(self):
        token = self.start()
        attacker = TestClient(api.app, base_url=BASE)
        attacker.cookies.set(auth.FLOW_COOKIE, "wrong", domain="papermind.test", path="/")
        self.assertEqual(self.complete(token, client=attacker).status_code, 400)
        self.assertEqual(self.complete(token).status_code, 303)
        self.client.cookies.set(auth.FLOW_COOKIE, "replay", domain="papermind.test", path="/")
        self.assertEqual(self.complete(token).status_code, 400)
        token = self.start()
        with auth.db() as conn:
            conn.execute("UPDATE flows SET expires=0")
        self.assertEqual(self.complete(token).status_code, 400)

    def test_identity_failure_does_not_create_account(self):
        self.assertEqual(self.complete(self.start(), zotero_id="invalid").status_code, 502)
        with auth.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM accounts").fetchone()[0], 0)

    def test_device_pair_is_one_time_and_same_identity_as_web(self):
        device = self.client.post("/api/auth/device/start", json={"legacy_uid": UID}).json()
        self.assertNotIn(device["secret"], device["verification_url"])
        self.assertTrue(self.client.post("/api/auth/device/poll", json={"secret": device["secret"]}).json()["pending"])
        self.complete(self.start({"adopt_legacy": True, "device_code": device["user_code"]}))
        polled = self.client.post("/api/auth/device/poll", json={"secret": device["secret"]}).json()
        self.assertFalse(polled["pending"])
        self.assertEqual(polled["account"]["id"], self.client.get("/api/auth/status").json()["account"]["id"])
        plugin = TestClient(api.app, base_url=BASE)
        self.assertEqual(plugin.get("/api/profile", headers={"Authorization": "Bearer " + polled["token"]}).status_code, 200)
        self.assertEqual(plugin.post("/api/auth/device/poll", json={"secret": device["secret"]}).status_code, 410)
        self.assertEqual(plugin.post("/api/auth/logout", json={}, headers={"Authorization": "Bearer " + polled["token"]}).status_code, 200)
        self.assertEqual(plugin.get("/api/profile", headers={"Authorization": "Bearer " + polled["token"]}).status_code, 401)

    def test_cancelled_device_cannot_be_completed(self):
        device = self.client.post("/api/auth/device/start", json={}).json()
        token = self.start({"device_code": device["user_code"]})
        self.client.post("/api/auth/device/cancel", json={"secret": device["secret"]})
        self.assertEqual(self.complete(token).status_code, 400)
        self.assertEqual(self.client.post("/api/auth/device/poll", json={"secret": device["secret"]}).status_code, 410)

    def test_returning_account_keeps_both_namespaces_without_merging(self):
        self.complete(self.start())
        account_id = self.client.get("/api/auth/status").json()["account"]["id"]
        token = self.start(headers={"Origin": BASE, "X-User-ID": OTHER})
        result = self.complete(token)
        self.assertIn("legacy-preserved", result.headers["location"])
        self.assertEqual(self.client.get("/api/auth/status").json()["account"]["id"], account_id)
        self.assertFalse(auth.namespace_claimed(OTHER))

    def test_two_accounts_cannot_claim_the_same_legacy_namespace(self):
        self.complete(self.start())
        response = self.complete(self.start(), zotero_id="43")
        self.assertEqual(response.status_code, 409)

    def test_unconfigured_disabled_and_ai_requires_login_when_enabled(self):
        self.assertEqual(self.client.post("/api/chat", json={}, headers={"X-User-ID": UID}).status_code, 401)
        with patch.dict(os.environ, {"ZOTERO_OAUTH_CLIENT_SECRET": ""}):
            self.assertFalse(self.client.get("/api/auth/status").json()["enabled"])
            self.assertEqual(self.client.post("/api/auth/zotero/start", json={}, headers=self.headers).status_code, 503)
            self.assertEqual(self.client.get("/api/profile", headers={"X-User-ID": UID}).status_code, 200)

    def test_guest_can_save_without_triggering_background_ai(self):
        with patch.object(api.threading, "Thread") as thread:
            saved = self.client.post("/api/library/save", json={"paper": {"title": "Guest paper", "abstract": "Synthetic abstract"}}, headers=self.headers)
            self.assertEqual(saved.status_code, 200, saved.text)
            profile = self.client.post("/api/profile", json={}, headers=self.headers)
            self.assertEqual(profile.status_code, 200, profile.text)
            thread.assert_not_called()
        for path in ("/api/profile/memory-recent", "/api/profile/merge-to-core", "/api/settings/test"):
            self.assertEqual(self.client.post(path, json={}, headers=self.headers).status_code, 401)

    def test_new_account_without_claim_does_not_take_old_records(self):
        self.complete(self.start({"adopt_legacy": False}))
        self.assertNotEqual(self.client.get("/api/auth/status").json()["account"]["data_uid"], UID)
        self.assertFalse(auth.namespace_claimed(UID))

    def test_bad_bearer_never_falls_back_and_session_expiry(self):
        self.assertEqual(self.client.get("/api/profile", headers={"X-User-ID": UID, "Authorization": "Bearer forged"}).status_code, 401)
        self.assertEqual(self.client.get("/api/profile", headers={"X-User-ID": UID, "Authorization": "Bearer "}).status_code, 401)
        self.complete(self.start())
        with auth.db() as conn:
            conn.execute("UPDATE sessions SET expires=0")
        self.assertEqual(self.client.get("/api/auth/status").status_code, 401)

    def test_rate_limit_and_symlink_store(self):
        for _ in range(30):
            self.assertEqual(self.client.post("/api/auth/device/start", json={}).status_code, 200)
        self.assertEqual(self.client.post("/api/auth/device/start", json={}).status_code, 429)
        target = Path(self.temp.name) / "linked.sqlite"
        target.symlink_to(auth.AUTH_DB)
        with patch.object(auth, "AUTH_DB", target), self.assertRaises(OSError):
            with auth.db():
                pass

    def test_oauth_signature_matches_rfc5849_style_known_vector(self):
        # OAuth 1.0 classic photos example, including query parameters.
        value = auth.oauth_header("GET", "http://photos.example.net/photos?file=vacation.jpg&size=original",
            "dpf43f3p2l4k3l03", "kd94hf93k423kf44", "nnch734d00sl2jdk", "pfkkdhi9sl3r4s00",
            nonce="kllo9940pd9333jh", timestamp=1191242096)
        self.assertIn('oauth_signature="tR3%2BTy81lMeYAr%2FFid0kMTYa%2FWM%3D"', value)


if __name__ == "__main__":
    unittest.main()
