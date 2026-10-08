"""Real loopback HTTP checks against a disposable source/data copy; no model calls."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, ProxyHandler, Request

UID = "11111111-1111-4111-8111-111111111111"


@contextmanager
def server(directory, env):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    runtime_env = {**env}
    if runtime_env.get("PAPERMIND_PUBLIC_ORIGIN") == "loopback":
        runtime_env["PAPERMIND_PUBLIC_ORIGIN"] = f"http://127.0.0.1:{port}"
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "api:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
                               cwd=directory / "papermind", env=runtime_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    opener = build_opener(ProxyHandler({}))
    def request(path, method="GET", identity=True, payload=None, extra_headers=None, with_body=False):
        headers = {"X-User-ID": UID} if identity else {}
        headers.update(extra_headers or {})
        if payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            with opener.open(Request(f"http://127.0.0.1:{port}" + path, method=method, headers=headers,
                                     data=json.dumps(payload).encode() if payload is not None else None), timeout=2) as response:
                return (response.status, json.load(response)) if with_body else response.status
        except HTTPError as error:
            error.close()
            return error.code
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("Isolated backend exited before readiness")
            try:
                if request("/api/zotero-plugin/update.json", identity=False) == 200:
                    break
            except (URLError, TimeoutError, ConnectionError):
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("Isolated backend did not become ready")
        yield request
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="papermind-http-") as name:
        target = Path(name)
        for folder in ("papermind", "papermind/src"):
            (target / folder).mkdir(parents=True, exist_ok=True)
            for path in (root / folder).glob("*.py"):
                if path.is_symlink():
                    raise ValueError("Refusing linked source")
                shutil.copyfile(path, target / folder / path.name)
        shutil.copytree(root / "zotero-plugin", target / "zotero-plugin")
        env = {**os.environ, **dict.fromkeys(("QWEN_API_KEY", "GLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "OWNER_UID", "SENTRY_DSN",
               "ZOTERO_OAUTH_CLIENT_KEY", "ZOTERO_OAUTH_CLIENT_SECRET", "PAPERMIND_PUBLIC_ORIGIN"), ""), "PAPERMIND_INVITE_ONLY": "false"}
        env.pop("PYTHONPATH", None)
        checks = [("/api/settings", 200), ("/api/library", 200), ("/api/pdf-proxy?url=https%3A%2F%2F127.0.0.1%2Fprivate", 400)]
        with server(target, env) as request:
            for path, expected in checks:
                actual = request(path)
                if actual != expected:
                    raise RuntimeError(f"HTTP check failed: expected {expected}, got {actual}")
            if request("/api/settings", identity=False) != 401:
                raise RuntimeError("Missing device ID must be denied")
            code, status = request("/api/auth/status", with_body=True)
            assert code == 200 and not status["enabled"]
            assert request("/api/auth/device/start", "POST", payload={}) == 503
        env["PAPERMIND_INVITE_ONLY"] = "true"
        with server(target, env) as request:
            for path in ("/api/library", "/api/pdf-url", "/api/export/ris-direct", "/api/lookup-paper"):
                if request(path) != 403:
                    raise RuntimeError("Uninvited trial access must be denied")
        env.update(PAPERMIND_INVITE_ONLY="false", PAPERMIND_PUBLIC_ORIGIN="loopback",
                   ZOTERO_OAUTH_CLIENT_KEY="synthetic", ZOTERO_OAUTH_CLIENT_SECRET="synthetic", PAPERMIND_REQUIRE_AI_LOGIN="true")
        with server(target, env) as request:
            code, status = request("/api/auth/status", with_body=True)
            assert code == 200 and status["enabled"] and not status["authenticated"]
            assert request("/api/chat", "POST", payload={}) == 401
            assert request("/api/profile", extra_headers={"Authorization": "Bearer invalid"}) == 401
            code, device = request("/api/auth/device/start", "POST", payload={"legacy_uid": UID}, with_body=True)
            assert code == 200 and device["secret"] not in device["verification_url"]
            code, waiting = request("/api/auth/device/poll", "POST", payload={"secret": device["secret"]}, with_body=True)
            assert code == 200 and waiting["pending"]
            assert request("/api/auth/device/cancel", "POST", payload={"secret": device["secret"]}) == 200
            assert request("/api/auth/device/poll", "POST", payload={"secret": device["secret"]}) == 410
        print("Real HTTP checks passed: library/settings, missing identity, private PDF target, invitation gate, auth status, AI login gate, device pairing/cancellation. Temporary services closed; no real data, Zotero provider or model calls.")


if __name__ == "__main__":
    main()
