"""Persistent daily AI attempts and OS-owned cross-process concurrency slots."""
from __future__ import annotations
import asyncio
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import sqlite3

STATE_DIR = Path(__file__).parent / "data" / "runtime-limits"


class RuntimeLimitError(RuntimeError):
    pass


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
    with ai_slot():
        return await asyncio.wait_for(client.chat.completions.create(**kwargs), timeout=90)


def completion_sync(client, **kwargs):
    with ai_slot():
        return client.chat.completions.create(**kwargs)
