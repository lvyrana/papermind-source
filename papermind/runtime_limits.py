"""AI attempts: local OS locks, or shared Turso admission for bounded async calls."""
from __future__ import annotations
import asyncio
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import fcntl
import logging
import os
from pathlib import Path
import sqlite3
import time
import uuid

from sql_backend import connect_remote, remote_enabled

STATE_DIR = Path(__file__).parent / "data" / "runtime-limits"
REMOTE_CALL_SECONDS = 45
# The Vercel entrypoint is capped at 60s. Keep orphaned slots longer than that
# bound; changing the deployment duration also requires reviewing this lease.
REMOTE_LEASE_SECONDS = 120
REMOTE_SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (day TEXT PRIMARY KEY, count INTEGER NOT NULL);
CREATE TABLE ai_policy (
  id INTEGER PRIMARY KEY CHECK(id=1),
  daily_limit INTEGER NOT NULL CHECK(daily_limit>0),
  concurrent_limit INTEGER NOT NULL CHECK(concurrent_limit>0)
);
INSERT INTO ai_policy VALUES (1,500,4);
CREATE TABLE ai_leases (id TEXT PRIMARY KEY, expires INTEGER NOT NULL);
CREATE INDEX ai_leases_expiry ON ai_leases(expires);
CREATE TABLE _papermind_runtime_schema (version INTEGER NOT NULL);
INSERT INTO _papermind_runtime_schema VALUES (1);
"""
logger = logging.getLogger(__name__)


class RuntimeLimitError(RuntimeError):
    status_code = 429


class RuntimeStoreUnavailable(RuntimeLimitError):
    status_code = 503


def _claim_remote():
    """Count one attempted call and acquire one slot in the same transaction.

    Server UTC controls both the date and expiration. The policy is shared too:
    per-deployment environment variables must not create extra allowances.
    Never retry an ambiguous commit, refund an attempt, or fall back to disk.
    """
    lease = uuid.uuid4().hex
    started = time.monotonic()
    try:
        with closing(connect_remote("runtime")) as conn:
            if conn.execute("SELECT version FROM _papermind_runtime_schema").fetchone()[0] != 1:
                raise ValueError("Unprepared runtime schema")
            try:
                conn.execute("BEGIN IMMEDIATE")
                now, day = conn.execute("SELECT CAST(strftime('%s','now') AS INTEGER),date('now')").fetchone()
                policy = conn.execute("SELECT daily_limit,concurrent_limit FROM ai_policy WHERE id=1").fetchone()
                if policy is None or min(policy) < 1:
                    raise ValueError("Missing runtime policy")
                conn.execute("DELETE FROM ai_leases WHERE expires<=?", (now,))
                active = conn.execute("SELECT count(*) FROM ai_leases").fetchone()[0]
                if active >= policy[1]:
                    raise RuntimeLimitError("AI 请求正在处理中，请稍后重试。")
                row = conn.execute("SELECT count FROM attempts WHERE day=?", (day,)).fetchone()
                if row and row[0] >= policy[0]:
                    raise RuntimeLimitError("今日 AI 服务请求预算已用完，UTC 零点后恢复。")
                conn.execute("INSERT INTO attempts VALUES (?,1) ON CONFLICT(day) DO UPDATE SET count=count+1", (day,))
                conn.execute("INSERT INTO ai_leases VALUES (?,?)", (lease, now + REMOTE_LEASE_SECONDS))
                conn.commit()
            except BaseException:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
    except RuntimeLimitError:
        raise
    except Exception:
        # Driver errors may include a URL/token; do not expose their text.
        raise RuntimeStoreUnavailable("AI 额度服务暂不可用，请稍后重试。") from None
    if time.monotonic() - started >= REMOTE_LEASE_SECONDS - REMOTE_CALL_SECONDS - 10:
        # Commit may have succeeded, but too little lease remains to start work.
        _release_remote(lease)
        raise RuntimeStoreUnavailable("AI 额度确认超时，请稍后重试。")
    return lease


def _release_remote(lease):
    try:
        with closing(connect_remote("runtime")) as conn:
            conn.execute("DELETE FROM ai_leases WHERE id=?", (lease,))
            conn.commit()
    except Exception:
        # Retain the conservative charge/lease on uncertainty. It expires on
        # server time, and this must not overwrite a provider/cancellation error.
        logger.warning("AI slot release unconfirmed; retained until lease expiry")


def positive_setting(name, default):
    value = int(os.environ.get(name, default))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


@contextmanager
def file_lock(path, blocking=True):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield
    finally:
        os.close(fd)


@contextmanager
def ai_slot():
    if remote_enabled():
        raise RuntimeStoreUnavailable("云端 AI 需要使用限时异步入口。")
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    slot = None
    for number in range(positive_setting("AI_MAX_CONCURRENT_REQUESTS", "4")):
        candidate = file_lock(STATE_DIR / f"slot-{number}.lock", blocking=False)
        try:
            candidate.__enter__()
        except BlockingIOError:
            continue
        slot = candidate
        break
    if slot is None:
        raise RuntimeLimitError("AI 请求正在处理中，请稍后重试。")
    try:
        database = STATE_DIR / "daily.sqlite3"
        fd = os.open(database, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(fd)
        with closing(sqlite3.connect(database, timeout=5)) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS attempts (day TEXT PRIMARY KEY, count INTEGER NOT NULL)")
            conn.execute("BEGIN IMMEDIATE")
            day = datetime.now(timezone.utc).date().isoformat()
            row = conn.execute("SELECT count FROM attempts WHERE day=?", (day,)).fetchone()
            count = row[0] if row else 0
            if count >= positive_setting("GLOBAL_DAILY_AI_REQUEST_LIMIT", "500"):
                raise RuntimeLimitError("今日 AI 服务请求预算已用完，UTC 零点后恢复。")
            conn.execute("INSERT INTO attempts VALUES (?,?) ON CONFLICT(day) DO UPDATE SET count=excluded.count", (day, count + 1))
        yield
    finally:
        slot.__exit__(None, None, None)


async def completion_async(client, **kwargs):
    if remote_enabled():
        if kwargs.get("stream"):
            raise RuntimeStoreUnavailable("云端流式 AI 尚未接入额度生命周期。")
        lease = _claim_remote()
        try:
            return await asyncio.wait_for(client.chat.completions.create(**kwargs), timeout=REMOTE_CALL_SECONDS)
        except asyncio.TimeoutError:
            # Do not let the router start another provider after using most of
            # the function's execution window.
            raise RuntimeStoreUnavailable("AI 请求超时，请稍后重试。") from None
        finally:
            _release_remote(lease)
    with ai_slot():
        return await asyncio.wait_for(client.chat.completions.create(**kwargs), timeout=90)


def completion_sync(client, **kwargs):
    if remote_enabled():
        raise RuntimeStoreUnavailable("云端同步 AI 任务尚未接入，请使用异步入口。")
    with ai_slot():
        return client.chat.completions.create(**kwargs)
