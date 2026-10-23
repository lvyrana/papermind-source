"""Exercise shared AI admission through Preview HTTP and a separate local process.

Only the marked synthetic runtime DB is modified. No provider/API key is used.
Temporarily narrow its policy, then restore it; never reset accumulated attempts.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "papermind"))
import auth
import runtime_limits as limits
from sql_backend import connect_remote, remote_credentials
from urllib.parse import urlsplit


def query(sql, parameters=()):
    with closing(connect_remote("runtime")) as conn:
        cursor = conn.execute(sql, parameters)
        rows = cursor.fetchall() if cursor.description else []
        conn.commit()
        return [tuple(row) for row in rows]


def main(url):
    if not re.fullmatch(r"https://litfinch-migration-preview-[a-z0-9]+-lvyranas-projects.vercel.app", url):
        raise ValueError("Wrong preview URL")
    os.environ["PAPERMIND_SQL_BACKEND"] = "turso"
    for role in ("runtime", "auth"):
        host = urlsplit(remote_credentials(role)[0]).hostname or ""
        if not (host.startswith("litfinch-" + role + "-preview-") and host.endswith(".turso.io")):
            raise ValueError("Wrong test database")
        with closing(connect_remote(role)) as conn:
            assert [tuple(row) for row in conn.execute("SELECT purpose,role FROM _papermind_preview_init").fetchall()] == [("litfinch-synthetic-business-v1", role)]
    run = uuid.uuid4().hex
    account = "synthetic-runtime-" + run
    token = None
    old_policy = None
    stage = "prepare"
    try:
        assert query("SELECT version FROM _papermind_runtime_schema") == [(1,)]
        assert query("SELECT count(*) FROM ai_leases WHERE expires>CAST(strftime('%s','now') AS INTEGER)") == [(0,)]
        day = query("SELECT date('now')")[0][0]
        baseline = query("SELECT COALESCE((SELECT count FROM attempts WHERE day=date('now')),0)")[0][0]
        old_policy = query("SELECT daily_limit,concurrent_limit FROM ai_policy WHERE id=1")[0]
        query("UPDATE ai_policy SET daily_limit=?,concurrent_limit=1 WHERE id=1", (baseline + 3,))
        with auth.db() as conn:
            conn.execute("INSERT INTO accounts VALUES(?,?,?,?,?)", (account, account, "Synthetic runtime", str(uuid.uuid4()), int(time.time())))
            token = auth.new_session(conn, account)
            conn.execute("UPDATE sessions SET expires=? WHERE hash=?", (int(time.time()) + 1800, auth.digest(token)))
        base_headers = {"x-vercel-trusted-oidc-idp-token": os.environ["VERCEL_OIDC_TOKEN"],
                        "x-litfinch-runtime-probe": "synthetic-only-v1"}
        with httpx.Client(base_url=url, headers=base_headers, timeout=40) as client:
            def request(mode="ok", authenticated=True):
                return client.post("/_migration/runtime", params={"mode": mode},
                                   headers={"Authorization": "Bearer " + token} if authenticated else {})
            stage = "auth_and_shared_concurrency"
            assert request(authenticated=False).status_code == 401
            with ThreadPoolExecutor(1) as pool:
                pending = pool.submit(request, "hold")
                # Wait only for the admission being tested, not resource setup.
                deadline = time.monotonic() + 20
                while not query("SELECT id FROM ai_leases WHERE expires>CAST(strftime('%s','now') AS INTEGER)"):
                    if pending.done() or time.monotonic() > deadline:
                        raise AssertionError("HTTP admission not observed")
                    time.sleep(0.2)
                try:
                    limits._claim_remote()
                    raise AssertionError("Separate process exceeded shared concurrency")
                except limits.RuntimeLimitError as error:
                    assert error.status_code == 429
                refused = request()
                assert refused.status_code == 429
                first = pending.result(timeout=30)
                assert first.status_code == 200
            stage = "failure_and_daily_budget"
            assert request("fail").status_code == 502
            # Local worker shares the same counter, with no real provider.
            class FakeClient:
                async def create(self, **_):
                    return "synthetic"
            fake = FakeClient()
            fake.chat = fake.completions = fake
            assert asyncio.run(limits.completion_async(fake)) == "synthetic"
            assert request().status_code == 429
            assert query("SELECT count FROM attempts WHERE day=?", (day,)) == [(baseline + 3,)]
            assert query("SELECT count(*) FROM ai_leases") == [(0,)]
            assert query("SELECT date('now')") == [(day,)]
            print(json.dumps({"ok": True, "run_id": run, "vercel_instance": first.json()["instance_id"],
                              "distinct_vercel_instances_observed": len({first.headers.get("x-litfinch-instance"), refused.headers.get("x-litfinch-instance")}),
                              "independent_local_worker": True, "attempts_added": 3, "leases_remaining": 0,
                              "checks": ["anonymous_denied", "cross_process_concurrency", "http_concurrency_429",
                                         "failure_charged", "shared_budget_429", "release_confirmed"], "real_model_calls": 0}))
        return 0
    except Exception as error:
        print(json.dumps({"ok": False, "stage": stage, "run_id": run, "error_type": type(error).__name__}))
        return 1
    finally:
        if old_policy:
            query("UPDATE ai_policy SET daily_limit=?,concurrent_limit=? WHERE id=1", old_policy)
        if token:
            with auth.db() as conn:
                conn.execute("DELETE FROM sessions WHERE hash=?", (auth.digest(token),))
        print(json.dumps({"run_id": run, "policy_restored": bool(old_policy), "synthetic_session_revoked": bool(token)}))


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1]))
    except Exception:
        print(json.dumps({"ok": False, "stage": "target_or_cleanup"}))
        sys.exit(1)
