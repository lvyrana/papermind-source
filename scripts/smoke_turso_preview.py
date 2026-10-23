"""One synthetic business flow against the protected preview; no real OAuth/AI."""
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit
import uuid

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "papermind"))
import auth


def main(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or
            not parsed.hostname.startswith("litfinch-migration-preview-") or
            not parsed.hostname.endswith("-lvyranas-projects.vercel.app") or
            parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment or parsed.path):
        raise RuntimeError("Expected the exact protected migration preview URL")
    if not os.environ.get("VERCEL_OIDC_TOKEN"):
        raise RuntimeError("Run using the linked project's development environment")
    os.environ["PAPERMIND_SQL_BACKEND"] = "turso"
    run_id = uuid.uuid4().hex
    users = [str(uuid.uuid4()), str(uuid.uuid4())]
    tokens = []
    stage = "seed_synthetic_accounts"
    try:
        with auth.db() as conn:
            marker = conn.execute("SELECT purpose, role FROM _papermind_preview_init").fetchone()
            if tuple(marker) != ("litfinch-synthetic-business-v1", "auth"):
                raise RuntimeError("Wrong synthetic database marker")
            for index, uid in enumerate(users):
                account = "synthetic-" + run_id + "-" + str(index)
                conn.execute("INSERT INTO accounts VALUES (?,?,?,?,?)",
                             (account, account, "Migration fixture", uid, int(time.time())))
                token = auth.new_session(conn, account)
                conn.execute("UPDATE sessions SET expires=? WHERE hash=?",
                             (int(time.time()) + 3600, auth.digest(token)))
                tokens.append(token)
        base_headers = {"x-vercel-trusted-oidc-idp-token": os.environ["VERCEL_OIDC_TOKEN"]}
        client = httpx.Client(base_url=url, headers=base_headers, timeout=45, follow_redirects=False)
        def request(path, method="GET", who=0, payload=None, headers=None):
            supplied = {"Authorization": "Bearer " + tokens[who]} if who is not None else {}
            supplied.update(headers or {})
            return client.request(method, path, json=payload, headers=supplied)
        with client:
            stage = "actual_frontend"
            response = request("/welcome", who=None)
            assert response.status_code == 200 and '<div id="root"' in response.text
            assert response.headers.get("X-Litfinch-Preview") == "synthetic-business-v1"
            stage = "remote_identity"
            response = request("/api/auth/status")
            assert response.status_code == 200 and response.json()["account"]["data_uid"] == users[0]
            stage = "save_library"
            title = "合成迁移文献 " + run_id[:8]
            response = request("/api/library/save", "POST", payload={
                "paper": {"title": title, "doi": "10.test/" + run_id,
                          "abstract": "Synthetic fixture, not research evidence.",
                          "summary_zh": "合成测试内容，不是真实文献。"}},
                headers={"X-User-ID": users[1]})
            assert response.status_code == 200 and response.json()["ok"]
            paper_id = response.json()["id"]
            stage = "save_note"
            response = request("/api/notes", "POST", payload={"paper_rowid": paper_id, "content": "合成笔记：云端持久化。"})
            assert response.status_code == 200 and response.json()["ok"]
            stage = "save_card"
            response = request("/api/cards", "POST", payload={
                "paper_rowid": paper_id, "card_type": "method", "title": "合成卡片", "content": "账号 A 的测试内容。"})
            assert response.status_code == 200 and response.json()["ok"]
            stage = "fresh_http_read"
            with httpx.Client(base_url=url, headers={**base_headers, "Authorization": "Bearer " + tokens[0]},
                              timeout=45, follow_redirects=False) as fresh:
                response = fresh.get("/api/library/" + str(paper_id))
                assert response.status_code == 200
                assert response.json()["paper"]["title"] == title and len(response.json()["notes"]) == 1
                assert len(fresh.get("/api/cards/" + str(paper_id)).json()["cards"]) == 1
            stage = "other_account_isolation"
            assert paper_id not in [p["id"] for p in request("/api/library", who=1).json()["papers"]]
            assert request("/api/library/" + str(paper_id), who=1).json().get("error") == "not found"
            response = request("/api/notes", "POST", who=1, payload={"paper_rowid": paper_id, "content": "Must not save"})
            assert response.status_code == 200 and response.json()["ok"] is False
            stage = "claimed_uid_cannot_bypass_login"
            assert request("/api/library", who=None, headers={"X-User-ID": users[0]}).status_code == 401
            stage = "logout_revokes_cloud_session"
            assert request("/api/auth/logout", "POST").status_code == 200
            assert request("/api/library").status_code == 401
            assert request("/api/library", who=1).status_code == 200
            assert request("/api/auth/logout", "POST", who=1).status_code == 200
            stage = "unfinished_features_closed"
            assert request("/api/chat", "POST", who=None, payload={}).status_code == 503
            assert request("/api/library/" + str(paper_id) + "/pdf", who=None).status_code == 401
            print(json.dumps({"ok": True, "run_id": run_id, "paper_id": paper_id,
                              "synthetic_accounts": 2, "notes": 1, "cards": 1,
                              "persisted_records_retained": True, "both_sessions_revoked": True,
                              "checks": ["actual_frontend", "remote_identity", "library_note_card_persistence",
                                         "verified_account_overrides_uid", "cross_account_read_write_denied",
                                         "claimed_uid_denied", "cloud_logout", "unfinished_features_closed"],
                              "real_oauth_tested": False}))
    except Exception:
        # Revoke only this run's generated sessions, including failed starts.
        # Leave fixture records available for diagnosis; never touch real users.
        try:
            with auth.db() as conn:
                for token in tokens:
                    conn.execute("DELETE FROM sessions WHERE hash=?", (auth.digest(token),))
            revoked = True
        except Exception:
            revoked = False
        print(json.dumps({"ok": False, "stage": stage, "run_id": run_id,
                          "possible_synthetic_records_retained": True,
                          "synthetic_sessions_revoked": revoked}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
