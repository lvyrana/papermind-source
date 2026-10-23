"""Protected, synthetic-only business preview; never a production entrypoint."""
import os
import asyncio
from pathlib import Path
import re
import sys
import uuid
from types import SimpleNamespace

if os.environ.get("VERCEL_ENV") != "preview":
    raise RuntimeError("This entrypoint is for the migration preview only")

# Deliberate preview defaults. No real OAuth/model credentials are deployed.
os.environ["PAPERMIND_SQL_BACKEND"] = "turso"
os.environ["PAPERMIND_INVITE_ONLY"] = "false"
os.environ["PAPERMIND_BACKGROUND_ENRICHMENT"] = "true"
os.environ["PAPERMIND_SYNTHETIC_TASKS"] = "true"
os.environ["PAPERMIND_FILE_BACKEND"] = "vercel-blob"
os.environ["PAPERMIND_PUBLIC_ORIGIN"] = "https://" + os.environ["VERCEL_URL"]
os.environ["ALLOWED_ORIGINS"] = os.environ["PAPERMIND_PUBLIC_ORIGIN"]
if any(os.environ.get(key) for key in (
        "ZOTERO_OAUTH_CLIENT_KEY", "ZOTERO_OAUTH_CLIENT_SECRET", "QWEN_API_KEY",
        "GLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "OWNER_UID", "SENTRY_DSN")):
    raise RuntimeError("Real service credentials do not belong in this preview")

# Only a seeded synthetic account can manage preview configuration. A UUID
# header alone is insufficient: cloud owner routes require an account session.
os.environ['OWNER_UID'] = '00000000-0000-4000-8000-000000000001'

sys.path.insert(0, str(Path(__file__).resolve().parent / "papermind"))
from api import app
from fastapi.responses import JSONResponse
from fastapi import HTTPException
import auth
from runtime_limits import completion_async, RuntimeLimitError
from sql_backend import connect_remote

INSTANCE_ID = uuid.uuid4().hex[:12]
# Only implemented database-backed paths are exposed. File/AI/config endpoints
# cannot silently write to ephemeral local storage or start background jobs.
ALLOWED = {
    "GET": re.compile(r"/api/(auth/status|maintenance/storage|tasks|file-storage|settings|profile|memory|usage|stats|portrait|reading-history|"
                      r"self-test/\d+|papers/\d+/ocr-pages|quotes/\d+|export/(?:notes-markdown|ris/\d+|bibtex/\d+)|"
                      r"library(?:/\d+(?:/pdf|/pdf-upload)?)?|notes/\d+|cards/\d+|projects|board/\d+(?:/figures/[\w.-]+|/export/(?:md|marp|pptx))?)"),
    "HEAD": re.compile(r"/api/library/\d+/pdf"),
    "POST": re.compile(r"/api/(auth/logout|maintenance/storage|profile|memory/stated/delete|quotes|reading-history|feedback|export/(?:ris-direct|bibtex-direct)|settings/custom-llm|tasks/[0-9a-f]{32}/(?:run|retry)|file-storage/cleanup|library/save|notes|cards|projects|board/\d+/(?:items|figure-upload(?:/[0-9a-f]{32})?)|library/\d+/pdf-upload(?:/[0-9a-f]{32})?)"),
    "PATCH": re.compile(r"/api/(memory|cards/\d+|projects/\d+|library/\d+/project|board/(?:\d+|items/\d+))"),
    "DELETE": re.compile(r"/api/(memory|quotes/\d+|chat/\d+|settings/custom-llm|notes/\d+|cards/\d+|projects/\d+|board/items/\d+|library/\d+(?:/pdf)?)"),
}


async def runtime_probe(request):
    """Preview-only fixed fake provider; cannot select a model, URL or SQL."""
    headers = {"Cache-Control": "no-store", "X-Litfinch-Instance": INSTANCE_ID}
    try:
        if request.method != "POST" or request.headers.get("x-litfinch-runtime-probe") != "synthetic-only-v1":
            raise HTTPException(403, "Synthetic probe only")
        auth.require_origin(request, native=True)
        account = auth.identity(request)
        if not account:
            raise HTTPException(401, "Synthetic account required")
        if not re.fullmatch(r"synthetic-runtime-[0-9a-f]{32}", account["id"]):
            raise HTTPException(403, "Synthetic account required")
        mode = request.query_params.get("mode", "ok")
        if mode not in {"ok", "hold", "fail"}:
            raise HTTPException(400, "Unknown synthetic mode")
        conn = connect_remote("runtime")
        try:
            marker = conn.execute("SELECT purpose,role FROM _papermind_preview_init").fetchall()
            if [tuple(row) for row in marker] != [("litfinch-synthetic-business-v1", "runtime")]:
                raise HTTPException(503, "Synthetic runtime not prepared")
        finally:
            conn.close()
        async def fake_create(**_):
            if mode == "hold":
                await asyncio.sleep(8)
            if mode == "fail":
                raise ValueError("synthetic provider failure")
            return "synthetic completion"
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
        await completion_async(client)
        return JSONResponse({"ok": True, "synthetic_only": True, "instance_id": INSTANCE_ID}, headers=headers)
    except HTTPException as error:
        return JSONResponse({"ok": False, "detail": error.detail}, status_code=error.status_code, headers=headers)
    except RuntimeLimitError as error:
        return JSONResponse({"ok": False, "detail": str(error)}, status_code=error.status_code, headers=headers)
    except Exception:
        return JSONResponse({"ok": False, "detail": "Synthetic call failed"}, status_code=502, headers=headers)


@app.middleware("http")
async def preview_scope(request, call_next):
    path = request.url.path
    if path == "/_migration/runtime":
        return await runtime_probe(request)
    if path == "/_migration/health" and request.method == "GET":
        return JSONResponse({"ok": True, "scope": "synthetic-business-preview",
                             "instance_id": INSTANCE_ID,
                             "region": os.environ.get("VERCEL_REGION", "unknown"),
                             "database": "turso", "real_oauth_enabled": False,
                             "files_enabled": True, "synthetic_tasks_enabled": True, "ai_enabled": False},
                            headers={"Cache-Control": "no-store"})
    if path.startswith("/api/"):
        allowed = ALLOWED.get(request.method)
        if allowed is None or not allowed.fullmatch(path):
            return JSONResponse({"ok": False, "detail": "该功能尚未接入迁移预览，正式站不受影响。"},
                                status_code=503, headers={"Cache-Control": "no-store"})
    response = await call_next(request)
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Litfinch-Preview"] = "synthetic-business-v1"
    response.headers["X-Litfinch-Instance"] = INSTANCE_ID
    return response
