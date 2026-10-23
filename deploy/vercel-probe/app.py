"""Synthetic runtime probe, not the product. No user data or model calls."""
import time
STARTED = time.perf_counter()

import importlib.metadata
import io
import platform
import os
import uuid
import libsql
import httpx
import openai
import sentry_sdk
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from pptx import Presentation
from remote_probe import ProbeFailure, run_checks

IMPORT_MS = round((time.perf_counter() - STARTED) * 1000)
INSTANCE_ID = uuid.uuid4().hex[:12]
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
def health():
    conn = libsql.connect(":memory:")
    try:
        conn.execute("CREATE TABLE fixture (value TEXT)")
        conn.execute("INSERT INTO fixture VALUES (?)", ("synthetic",))
        conn.commit()
        valid = conn.execute("SELECT value FROM fixture").fetchone()[0] == "synthetic"
    finally:
        conn.close()
    buffer = io.BytesIO()
    Presentation().save(buffer)
    return JSONResponse({
        "purpose": "synthetic-runtime-probe",
        "python": platform.python_version(),
        "libsql": importlib.metadata.version("libsql"),
        "libsql_local_roundtrip": valid,
        "pptx_generated": buffer.tell() > 0,
        "module_import_ms": IMPORT_MS,
        "instance_id": INSTANCE_ID,
        "region": os.environ.get("VERCEL_REGION", "local"),
        "remote_database_tested": False,
        "product_deployed": False,
    }, headers={"Cache-Control": "no-store"})


@app.post("/checks/database")
def database_check(x_litfinch_probe: str = Header(default="")):
    # Vercel Deployment Protection stays enabled. Additional guards prevent
    # accidental production use and automatic GET/link-preview writes.
    if os.environ.get("VERCEL_ENV") != "preview" or x_litfinch_probe != "synthetic-v1":
        return JSONResponse({"ok": False, "stage": "preview_only"}, status_code=403)
    try:
        result = run_checks()
    except ProbeFailure as error:
        return JSONResponse({"ok": False, "stage": str(error)}, status_code=503,
                            headers={"Cache-Control": "no-store"})
    result.update({"instance_id": INSTANCE_ID,
                   "region": os.environ.get("VERCEL_REGION", "unknown"),
                   "module_import_ms": IMPORT_MS})
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
