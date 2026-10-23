"""Zotero identity-only OAuth and revocable PaperMind sessions.

No Zotero library keys are retained. Existing data namespaces are attached to a
verified account without rewriting research rows. Auth state has its own DB.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sql_backend import connect_remote, remote_enabled

AUTH_DB = Path(__file__).parent / "data" / "auth.sqlite3"
SESSION_COOKIE = "pm_session"
FLOW_COOKIE = "pm_oauth_flow"
FLOW_TTL = 600
SESSION_TTL = 30 * 86400
router = APIRouter(prefix="/api/auth")
REQUEST_URL = "https://www.zotero.org/oauth/request"
ACCESS_URL = "https://www.zotero.org/oauth/access"
AUTHORIZE_URL = "https://www.zotero.org/oauth/authorize"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def origin():
    value = os.environ.get("PAPERMIND_PUBLIC_ORIGIN", "").rstrip("/")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"})) \
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or not parsed.netloc:
        raise HTTPException(503, "登录服务尚未配置有效的站点地址。")
    return value


def enabled():
    return bool(os.environ.get("ZOTERO_OAUTH_CLIENT_KEY") and os.environ.get("ZOTERO_OAUTH_CLIENT_SECRET")
                and os.environ.get("PAPERMIND_PUBLIC_ORIGIN"))


def ai_login_required():
    return enabled() and os.environ.get("PAPERMIND_REQUIRE_AI_LOGIN", "true").lower() == "true"


def can_use_ai(request):
    return not ai_login_required() or bool(identity(request))


@contextmanager
def db(*, readonly=False):
    if remote_enabled():
        conn = connect_remote("auth")
        try:
            # Identity/ownership lookups are single SELECTs. A remote write
            # transaction here serializes the reader's parallel API requests
            # and can expire while queued behind other reads.
            if not readonly:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if not readonly:
                conn.commit()
        except BaseException:
            if not readonly:
                try:
                    conn.rollback()
                except Exception:
                    pass  # Preserve the original failure if Turso rolled back already.
            raise
        finally:
            conn.close()
        return
    AUTH_DB.parent.mkdir(parents=True, exist_ok=True)
    # Fail closed on a symlink; do not follow it to an unrelated file.
    fd = os.open(AUTH_DB, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    os.chmod(AUTH_DB, 0o600)
    conn = sqlite3.connect(AUTH_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
          id TEXT PRIMARY KEY, zotero_id TEXT NOT NULL UNIQUE,
          username TEXT NOT NULL, data_uid TEXT NOT NULL UNIQUE, created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
          hash TEXT PRIMARY KEY, account_id TEXT NOT NULL, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS flows (
          token TEXT PRIMARY KEY, secret TEXT NOT NULL, browser_hash TEXT NOT NULL,
          legacy_uid TEXT NOT NULL, device_code TEXT, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS devices (
          code TEXT PRIMARY KEY, poll_hash TEXT NOT NULL UNIQUE, legacy_uid TEXT NOT NULL,
          account_id TEXT, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts (ip_hash TEXT NOT NULL, created INTEGER NOT NULL);
        """)
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def throttle(request):
    # Rate limiting is supplementary; request tokens and device secrets remain
    # unguessable. Use the ASGI peer, not attacker-controlled forwarding headers.
    peer = request.client.host if request.client else "unknown"
    now = int(time.time())
    with db() as conn:
        conn.execute("DELETE FROM attempts WHERE created < ?", (now - 600,))
        count = conn.execute("SELECT count(*) FROM attempts WHERE ip_hash=?", (digest(peer),)).fetchone()[0]
        if count >= 30:
            raise HTTPException(429, "登录请求过于频繁，请稍后重试。")
        conn.execute("INSERT INTO attempts VALUES (?,?)", (digest(peer), now))
        for table in ("flows", "devices", "sessions"):
            conn.execute(f"DELETE FROM {table} WHERE expires < ?", (now,))


def require_origin(request, native=False):
    supplied = request.headers.get("origin")
    if native and not supplied:
        return
    if supplied != origin():
        raise HTTPException(403, "请从 PaperMind 本站发起此操作。")


def canonical_uid(value):
    try:
        uid = str(UUID(value))
        return uid if UUID(uid).version == 4 and uid == value.lower() else ""
    except (ValueError, TypeError, AttributeError):
        return ""


def oauth_header(method, url, key, secret, token="", token_secret="", extra=None, nonce=None, timestamp=None):
    """RFC 5849 HMAC-SHA1. Only fixed Zotero endpoints are used by callers."""
    params = {"oauth_consumer_key": key, "oauth_nonce": nonce or secrets.token_hex(20),
              "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": str(timestamp or int(time.time())),
              "oauth_version": "1.0", **(extra or {})}
    if token:
        params["oauth_token"] = token
    esc = lambda text: quote(str(text), safe="~-._")
    parsed = urlsplit(url)
    pairs = [(esc(k), esc(v)) for k, v in list(params.items()) + parse_qsl(parsed.query)]
    normalized = "&".join(f"{k}={v}" for k, v in sorted(pairs))
    base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    base = "&".join(map(esc, [method.upper(), base_url, normalized]))
    signing_key = f"{esc(secret)}&{esc(token_secret)}"
    params["oauth_signature"] = base64.b64encode(hmac.new(signing_key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{esc(k)}="{esc(v)}"' for k, v in sorted(params.items()))


async def exchange(url, token="", token_secret="", extra=None):
    header = oauth_header("POST", url, os.environ["ZOTERO_OAUTH_CLIENT_KEY"],
                          os.environ["ZOTERO_OAUTH_CLIENT_SECRET"], token, token_secret, extra)
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.post(url, headers={"Authorization": header})
        if response.status_code != 200:
            raise HTTPException(502, "Zotero 授权未完成，请重新登录。")
        pairs = parse_qsl(response.text, keep_blank_values=True)
        if len(pairs) != len(dict(pairs)):
            raise HTTPException(502, "Zotero 授权响应无效。")
        return dict(pairs)
    except httpx.HTTPError:
        raise HTTPException(502, "暂时无法连接 Zotero，请稍后重试。") from None


def cookie(response, name, value, max_age):
    response.set_cookie(name, value, max_age=max_age, httponly=True,
                        secure=origin().startswith("https:"), samesite="lax", path="/")


def no_store(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def identity(request):
    """Return authenticated account, never fall back to a UID after bad auth."""
    headers, cookies = getattr(request, "headers", {}), getattr(request, "cookies", {})
    authorization = headers.get("Authorization", "")
    if authorization:
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "登录凭据无效。")
        token = authorization[7:]
        if not token or len(token) > 256:
            raise HTTPException(401, "登录凭据无效。")
    else:
        token = cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    if not remote_enabled() and not AUTH_DB.exists():
        raise HTTPException(401, "登录已过期，请重新登录。")
    with db(readonly=True) as conn:
        row = conn.execute("SELECT a.* FROM accounts a JOIN sessions s ON a.id=s.account_id WHERE s.hash=? AND s.expires>?",
                           (digest(token), int(time.time()))).fetchone()
    if not row:
        raise HTTPException(401, "登录已过期，请重新登录。")
    if not authorization and getattr(request, "method", "GET") not in {"GET", "HEAD", "OPTIONS"}:
        require_origin(request)
    return dict(row)


def namespace_claimed(uid):
    if not remote_enabled() and not AUTH_DB.exists():
        return False
    with db(readonly=True) as conn:
        return bool(conn.execute("SELECT 1 FROM accounts WHERE data_uid=?", (uid,)).fetchone())


def public_account(account):
    return {"id": account["id"], "username": account["username"], "data_uid": account["data_uid"]}


def new_session(conn, account_id):
    token = secrets.token_urlsafe(40)
    conn.execute("INSERT INTO sessions VALUES (?,?,?)", (digest(token), account_id, int(time.time()) + SESSION_TTL))
    return token


class Start(BaseModel):
    adopt_legacy: bool = False
    device_code: str = Field(default="", max_length=16)


class DeviceStart(BaseModel):
    legacy_uid: str = Field(default="", max_length=64)


class DevicePoll(BaseModel):
    secret: str = Field(min_length=32, max_length=100)


@router.get("/status")
def status(request: Request):
    account = identity(request)
    return no_store(JSONResponse({"enabled": enabled(), "ai_login_required": ai_login_required(), "authenticated": bool(account),
                                  "account": public_account(account) if account else None}))


@router.post("/zotero/start")
async def start(body: Start, request: Request):
    if not enabled():
        raise HTTPException(503, "Zotero 登录尚未启用，现有本地阅读记录不受影响。")
    require_origin(request)
    throttle(request)
    legacy = canonical_uid(request.headers.get("X-User-ID", "")) if body.adopt_legacy else ""
    if body.device_code:
        with db() as conn:
            device = conn.execute("SELECT * FROM devices WHERE code=? AND expires>? AND account_id IS NULL",
                                  (body.device_code, int(time.time()))).fetchone()
        if not device:
            raise HTTPException(400, "插件连接已过期，请从插件重新发起。")
        legacy = device["legacy_uid"] if body.adopt_legacy else ""
    result = await exchange(REQUEST_URL, extra={"oauth_callback": origin() + "/api/auth/zotero/callback"})
    if not result.get("oauth_token") or not result.get("oauth_token_secret") or result.get("oauth_callback_confirmed") != "true":
        raise HTTPException(502, "Zotero 未确认回调地址，请检查应用配置。")
    browser_secret = secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute("INSERT INTO flows VALUES (?,?,?,?,?,?)", (result["oauth_token"], result["oauth_token_secret"],
                     digest(browser_secret), legacy, body.device_code or None, int(time.time()) + FLOW_TTL))
    url = AUTHORIZE_URL + "?" + urlencode({"oauth_token": result["oauth_token"], "identity": "1"})
    response = no_store(JSONResponse({"authorize_url": url}))
    cookie(response, FLOW_COOKIE, browser_secret, FLOW_TTL)
    return response


@router.get("/zotero/callback")
async def callback(request: Request):
    if not enabled():
        raise HTTPException(503, "登录服务未启用。")
    token = request.query_params.get("oauth_token", "")
    verifier = request.query_params.get("oauth_verifier", "")
    browser_secret = request.cookies.get(FLOW_COOKIE, "")
    if not token or not verifier or not browser_secret or len(token) > 512 or len(verifier) > 512:
        return no_store(RedirectResponse(origin() + "/settings?auth=cancelled", status_code=303))
    with db() as conn:
        flow = conn.execute("SELECT * FROM flows WHERE token=? AND expires>?", (token, int(time.time()))).fetchone()
        if not flow or not hmac.compare_digest(flow["browser_hash"], digest(browser_secret)):
            raise HTTPException(400, "登录请求已过期或不属于当前浏览器，请重新开始。")
        # One use, including failed exchanges. A retry starts a fresh handshake.
        conn.execute("DELETE FROM flows WHERE token=?", (token,))
    result = await exchange(ACCESS_URL, token, flow["secret"], {"oauth_verifier": verifier})
    zotero_id = result.get("userID", "")
    if not zotero_id.isdigit() or not 0 < int(zotero_id) < 2**63:
        raise HTTPException(502, "Zotero 没有返回可验证的账号身份。")
    # identity=1 needs no library API key. Never retain any returned key/secret.
    username = str(result.get("username", "Zotero 用户"))[:100]
    legacy = flow["legacy_uid"]
    with db() as conn:
        if flow["device_code"]:
            device = conn.execute("SELECT * FROM devices WHERE code=? AND expires>? AND account_id IS NULL",
                                  (flow["device_code"], int(time.time()))).fetchone()
            if not device:
                raise HTTPException(400, "插件连接已取消或过期，请重新发起。")
        account = conn.execute("SELECT * FROM accounts WHERE zotero_id=?", (zotero_id,)).fetchone()
        if not account:
            if legacy and conn.execute("SELECT 1 FROM accounts WHERE data_uid=?", (legacy,)).fetchone():
                raise HTTPException(409, "这份旧记录已关联其他账号；未改变记录归属。")
            account_id, uid = str(uuid4()), legacy or str(uuid4())
            conn.execute("INSERT INTO accounts VALUES (?,?,?,?,?)", (account_id, zotero_id, username, uid, int(time.time())))
            account = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        else:
            conn.execute("UPDATE accounts SET username=? WHERE id=?", (username, account["id"]))
        session = new_session(conn, account["id"])
        if flow["device_code"]:
            conn.execute("UPDATE devices SET account_id=? WHERE code=?", (account["id"], flow["device_code"]))
        # A returning account with a different guest namespace keeps both intact.
        preserved = bool(legacy and legacy != account["data_uid"])
    destination = "/settings?auth=" + ("legacy-preserved" if preserved else "success")
    if flow["device_code"]:
        destination += "&connected=plugin"
    response = no_store(RedirectResponse(origin() + destination, status_code=303))
    cookie(response, SESSION_COOKIE, session, SESSION_TTL)
    response.delete_cookie(FLOW_COOKIE, path="/")
    return response


@router.post("/device/start")
def device_start(body: DeviceStart, request: Request):
    if not enabled():
        raise HTTPException(503, "Zotero 登录尚未启用，连接设置中的原有专属链接仍可使用。")
    require_origin(request, native=True)
    throttle(request)
    secret = secrets.token_urlsafe(40)
    code = secrets.token_hex(6).upper()
    with db() as conn:
        conn.execute("INSERT INTO devices VALUES (?,?,?,?,?)", (code, digest(secret), canonical_uid(body.legacy_uid), None, int(time.time()) + FLOW_TTL))
    return no_store(JSONResponse({"secret": secret, "user_code": code, "expires_in": FLOW_TTL,
                                  "verification_url": origin() + "/settings?device=" + code, "interval": 3}))


@router.post("/device/poll")
def device_poll(body: DevicePoll, request: Request):
    require_origin(request, native=True)
    with db() as conn:
        device = conn.execute("SELECT * FROM devices WHERE poll_hash=? AND expires>?", (digest(body.secret), int(time.time()))).fetchone()
        if not device:
            raise HTTPException(410, "插件登录请求已取消、过期或使用。")
        if not device["account_id"]:
            return no_store(JSONResponse({"pending": True}))
        account = conn.execute("SELECT * FROM accounts WHERE id=?", (device["account_id"],)).fetchone()
        token = new_session(conn, account["id"])
        conn.execute("DELETE FROM devices WHERE code=?", (device["code"],))
    return no_store(JSONResponse({"pending": False, "token": token, "expires_in": SESSION_TTL, "account": public_account(account)}))


@router.post("/device/cancel")
def device_cancel(body: DevicePoll, request: Request):
    require_origin(request, native=True)
    with db() as conn:
        conn.execute("DELETE FROM devices WHERE poll_hash=?", (digest(body.secret),))
    return no_store(JSONResponse({"ok": True}))


@router.post("/logout")
def logout(request: Request):
    # A stale session can still be cleared. Bearer clients need no cookie CSRF.
    authorization = request.headers.get("Authorization", "")
    require_origin(request, native=authorization.startswith("Bearer "))
    token = authorization[7:] if authorization.startswith("Bearer ") else request.cookies.get(SESSION_COOKIE, "")
    if token and (remote_enabled() or AUTH_DB.exists()):
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE hash=?", (digest(token),))
    response = no_store(JSONResponse({"ok": True}))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
