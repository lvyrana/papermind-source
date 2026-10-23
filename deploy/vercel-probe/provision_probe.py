"""Explicit post-provision checks. Synthetic bytes only; no local file inputs.

This CLI helper is deliberately excluded from the Vercel upload allowlist.
It never prints credentials, database URLs, or private delivery URLs.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
import time
from urllib.parse import urlsplit
import uuid

import httpx
import libsql

ROLES = ("library", "auth", "runtime")
LARGE_SIZE = 12_503_468


class ProvisionFailure(RuntimeError):
    pass


def database_checks():
    settings = []
    for role in ROLES:
        prefix = "PAPERMIND_" + role.upper() + "_TURSO_"
        url = os.environ.get(prefix + "DATABASE_URL", "").strip()
        token = os.environ.get(prefix + "AUTH_TOKEN", "").strip()
        try:
            parsed = urlsplit(url)
            valid = (parsed.scheme in {"libsql", "https"} and parsed.hostname and
                     parsed.hostname.endswith(".turso.io") and token and
                     not (parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment) and
                     parsed.path in {"", "/"})
        except ValueError:
            valid = False
        if not valid:
            raise ProvisionFailure(role + "_configuration")
        settings.append((role, url, token, parsed.hostname.lower()))
    if len({host for _, _, _, host in settings}) != len(ROLES):
        raise ProvisionFailure("role_databases_not_distinct")
    results = {}
    for role, url, token, _ in settings:
        started = time.perf_counter()
        try:
            with closing(libsql.connect(url, auth_token=token, timeout=5)) as conn:
                connected = conn.execute("SELECT 1").fetchone()[0] == 1
                count = conn.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchone()[0]
            results[role] = {"connected": connected, "user_tables": count,
                             "elapsed_ms": round((time.perf_counter() - started) * 1000)}
        except Exception:
            raise ProvisionFailure(role + "_read") from None
    return {"ok": all(result["connected"] for result in results.values()),
            "distinct_databases": True, "writes_performed": False, "databases": results}


def synthetic_content(size):
    if size not in (4096, LARGE_SIZE):
        raise ProvisionFailure("unsupported_synthetic_size")
    pattern = b"LitFinch synthetic storage fixture. No document or account data.\n"
    return (pattern * (size // len(pattern) + 1))[:size]


def blob_checks(size=4096):
    # The SDK resolves short-lived OIDC from the process environment. No .env
    # reads and no persistent read-write token are needed in this helper.
    from vercel.blob import sync as blob
    body = synthetic_content(size)
    digest = hashlib.sha256(body).hexdigest()
    pathname = "migration-synthetic-v1/" + uuid.uuid4().hex + ".bin"
    stage = "private_upload"
    started = time.perf_counter()
    try:
        uploaded = blob.put_blob(pathname, body, access="private",
                                 content_type="application/octet-stream",
                                 add_random_suffix=False, allow_overwrite=False)
        stage = "private_metadata"
        if blob.head_blob(uploaded.url).size != size:
            raise ProvisionFailure(stage)
        stage = "private_stream_read"
        received = hashlib.sha256()
        received_size = 0
        with blob.stream_blob(uploaded.url, access="private") as download:
            for chunk in download:
                received.update(chunk)
                received_size += len(chunk)
        if received_size != size or received.hexdigest() != digest:
            raise ProvisionFailure("private_hash_mismatch")
        stage = "anonymous_access"
        # A separate HTTP client has no SDK Authorization headers or cookies.
        parsed = urlsplit(uploaded.url)
        if (parsed.scheme != "https" or not parsed.hostname or
                not parsed.hostname.endswith(".private.blob.vercel-storage.com") or
                parsed.username or parsed.password or parsed.query):
            raise ProvisionFailure("unexpected_delivery_url")
        with httpx.stream("GET", uploaded.url, headers={"Range": "bytes=0-0"},
                          follow_redirects=False, timeout=30) as response:
            anonymous_status = response.status_code
        if anonymous_status not in (401, 403, 404):
            raise ProvisionFailure("anonymous_access_not_denied")
    except Exception:
        raise ProvisionFailure(stage) from None
    return {"ok": True, "synthetic_only": True, "pathname": pathname,
            "bytes": size, "sha256": digest, "private_roundtrip": True,
            "anonymous_status": anonymous_status,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "retained": True, "browser_direct_upload_tested": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", choices=("databases", "blob", "blob-large"))
    args = parser.parse_args()
    try:
        result = (database_checks() if args.check == "databases" else
                  blob_checks(LARGE_SIZE if args.check == "blob-large" else 4096))
    except ProvisionFailure as error:
        print(json.dumps({"ok": False, "stage": str(error)}))
        raise SystemExit(1)
    print(json.dumps(result))
