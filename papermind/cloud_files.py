"""Private PDF reservations; no disk fallback and no client-chosen blob URLs."""
from contextlib import contextmanager
import asyncio
import hashlib
import os
import re
import time
import uuid

from fastapi import HTTPException
from src import database

MAX_PDF = 50 * 1024 * 1024
# Application grace period, not a promise about Blob's upload timeout. Retain
# tombstones and re-sweep them so a late in-flight PUT is not forgotten.
GC_GRACE = 3600
GC_RECHECK = 3600
GC_SCHEMA = """
CREATE TABLE pdf_cleanup (upload_id TEXT PRIMARY KEY, checked_at INTEGER NOT NULL);
"""
SCHEMA = """
CREATE TABLE pdf_uploads (
 id TEXT PRIMARY KEY, paper_id INTEGER NOT NULL, user_id TEXT NOT NULL,
 pathname TEXT NOT NULL UNIQUE, size INTEGER NOT NULL CHECK(size > 0),
 state TEXT NOT NULL CHECK(state IN ('pending','ready','retired')),
 expires INTEGER NOT NULL, created INTEGER NOT NULL, sha256 TEXT);
CREATE INDEX pdf_uploads_owner ON pdf_uploads(user_id, paper_id, state);
CREATE UNIQUE INDEX pdf_uploads_current ON pdf_uploads(paper_id) WHERE state='ready';
CREATE TABLE _papermind_file_schema (version INTEGER PRIMARY KEY);
INSERT INTO _papermind_file_schema VALUES (2);
""" + GC_SCHEMA


def enabled():
    return os.environ.get("PAPERMIND_FILE_BACKEND") == "vercel-blob"


@contextmanager
def transaction():
    conn = database._ensure_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def owned(conn, paper_id, uid):
    row = conn.execute("SELECT id FROM saved_papers WHERE id=? AND user_id=?", (paper_id, uid)).fetchone()
    if not row:
        raise HTTPException(404, "not found")


def check_quota(conn, uid, size):
    tables = [('pdf_uploads', 'pdf_cleanup')]
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='figure_uploads'").fetchone():
        tables.append(('figure_uploads', 'figure_cleanup'))
    total, count, user_size = 0, 0, 0
    for uploads, cleanup_table in tables:
        row = conn.execute(f'SELECT COALESCE(SUM(size),0),COUNT(*),COALESCE(SUM(CASE WHEN user_id=? THEN size ELSE 0 END),0) FROM {uploads} WHERE id NOT IN (SELECT upload_id FROM {cleanup_table})', (uid,)).fetchone()
        total += row[0]
        count += row[1]
        user_size += row[2]
    if total + size > 200 * 1024 * 1024 or user_size + size > 100 * 1024 * 1024 or count >= 100:
        raise HTTPException(413, '文件配额已满，请先清理附件。')


def reserve(paper_id, uid, size):
    if type(size) is not int or not 5 <= size <= MAX_PDF:
        raise HTTPException(413, "PDF 大小必须在 5 字节至 50MB 之间。")
    now = int(time.time())
    with transaction() as conn:
        owned(conn, paper_id, uid)
        pending = conn.execute("SELECT * FROM pdf_uploads WHERE paper_id=? AND state='pending' AND expires>? ORDER BY created DESC LIMIT 1",
                               (paper_id, now)).fetchone()
        if pending:
            # A repeat token request for the same upload is safe; overwrites are forbidden.
            if pending["size"] != size:
                raise HTTPException(409, "另一份 PDF 正在上传，请稍后重试。")
            return dict(pending)
        # Pending/retired objects keep counting until explicitly cleaned up.
        check_quota(conn, uid, size)
        upload_id = uuid.uuid4().hex
        pathname = "paper-pdf-v1/" + upload_id + ".pdf"
        conn.execute("INSERT INTO pdf_uploads VALUES (?,?,?,?,?,'pending',?,?,NULL)",
                     (upload_id, paper_id, uid, pathname, size, now + 600, now))
        return dict(conn.execute("SELECT * FROM pdf_uploads WHERE id=?", (upload_id,)).fetchone())


def current(paper_id, uid):
    conn = database._ensure_db()
    try:
        owned(conn, paper_id, uid)
        row = conn.execute("SELECT * FROM pdf_uploads WHERE paper_id=? AND user_id=? AND state='ready'", (paper_id, uid)).fetchone()
        if not row:
            raise HTTPException(404, "PDF not found")
        return dict(row)
    finally:
        conn.close()


def retire(paper_id, uid, *, delete_paper=False):
    """Immediately revoke app access, atomically retaining storage ownership."""
    with transaction() as conn:
        owned(conn, paper_id, uid)
        conn.execute("UPDATE pdf_uploads SET state='retired' WHERE paper_id=? AND user_id=? AND state!='retired'", (paper_id, uid))
        conn.execute("UPDATE saved_papers SET has_pdf=0 WHERE id=? AND user_id=?", (paper_id, uid))
        if delete_paper:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='figure_uploads'").fetchone():
                conn.execute("UPDATE figure_uploads SET state='retired' WHERE paper_id=? AND user_id=?", (paper_id, uid))
            database.delete_saved_paper(paper_id, connection=conn)


async def cleanup(uid, *, limit=3):
    """Bounded, user-scoped sweep. Never accept paths or deletion times from API callers."""
    from vercel.blob import delete_blob, head_blob, BlobNotFoundError
    now = int(time.time())
    with transaction() as conn:
        rows = [dict(row) for row in conn.execute("""
            SELECT p.* FROM pdf_uploads p LEFT JOIN pdf_cleanup g ON g.upload_id=p.id
            WHERE p.user_id=? AND p.state IN ('pending','retired') AND p.expires<?
              AND (g.upload_id IS NULL OR g.checked_at<?)
            ORDER BY (g.upload_id IS NOT NULL),p.created,p.id LIMIT ?
            """, (uid, now - GC_GRACE, now - GC_RECHECK, max(1, min(limit, 10)))).fetchall()]
        for row in rows:
            conn.execute("UPDATE pdf_uploads SET state='retired' WHERE id=? AND user_id=?", (row['id'], uid))
            # Rechecked tombstones count again until absence is reconfirmed.
            conn.execute("DELETE FROM pdf_cleanup WHERE upload_id=?", (row['id'],))
    cleared, failed = 0, 0
    for row in rows:
        if not re.fullmatch(r'[0-9a-f]{32}', row['id']) or row['pathname'] != 'paper-pdf-v1/' + row['id'] + '.pdf':
            failed += 1
            continue
        try:
            async with asyncio.timeout(8):
                await delete_blob(row['pathname'])
                try:
                    await head_blob(row['pathname'])
                except BlobNotFoundError:
                    pass
                else:
                    raise RuntimeError('Blob deletion not yet confirmed')
            with transaction() as conn:
                conn.execute("INSERT INTO pdf_cleanup VALUES (?,?) ON CONFLICT(upload_id) DO UPDATE SET checked_at=excluded.checked_at",
                             (row['id'], int(time.time())))
            cleared += 1
        except Exception:
            # No raw SDK errors: they can contain credentials or private paths.
            failed += 1
    conn = database._ensure_db()
    try:
        pending = conn.execute("SELECT COUNT(*) FROM pdf_uploads WHERE user_id=? AND state='retired' AND id NOT IN (SELECT upload_id FROM pdf_cleanup)", (uid,)).fetchone()[0]
    finally:
        conn.close()
    return {'reclaimed': cleared, 'failed': failed, 'cleanup_pending': pending}


async def download(paper_id, uid):
    from fastapi.responses import StreamingResponse
    from vercel.blob import stream_blob
    row = current(paper_id, uid)
    context = stream_blob(row["pathname"], access="private")
    try:
        source = await context.__aenter__()
    except Exception:
        raise HTTPException(502, "PDF 暂时无法读取，请重试。") from None

    async def chunks():
        try:
            async for chunk in source:
                yield chunk
        finally:
            await context.__aexit__(None, None, None)
    return StreamingResponse(chunks(), media_type="application/pdf", headers={
        "Content-Length": str(row["size"]), "Content-Disposition": "inline; filename=paper.pdf",
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


async def complete(paper_id, uid, upload_id):
    with transaction() as conn:
        owned(conn, paper_id, uid)
        row = conn.execute("SELECT * FROM pdf_uploads WHERE id=? AND paper_id=? AND user_id=?",
                           (upload_id, paper_id, uid)).fetchone()
        if not row:
            raise HTTPException(404, "not found")
        row = dict(row)
        if row["state"] == "ready":
            return {"ok": True, "size": row["size"], "sha256": row["sha256"]}
        if row["state"] != "pending" or row["expires"] < time.time():
            raise HTTPException(409, "上传已过期，请重新上传。")
    from vercel.blob import stream_blob
    digest, size, prefix = hashlib.sha256(), 0, b""
    try:
        async with stream_blob(row["pathname"], access="private") as download:
            if download.metadata.content_type != "application/pdf":
                raise HTTPException(415, "只支持 PDF 文件。")
            async for chunk in download:
                size += len(chunk)
                if size > row["size"]:
                    raise HTTPException(413, "上传文件大小与声明不一致。")
                prefix = (prefix + chunk)[:5] if len(prefix) < 5 else prefix
                digest.update(chunk)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "未能读取上传文件，请重试完成上传。") from None
    if prefix != b"%PDF-" or size != row["size"]:
        raise HTTPException(415, "文件不是有效的 PDF，或上传尚未完成。")
    with transaction() as conn:
        owned(conn, paper_id, uid)
        latest = conn.execute("SELECT state,expires FROM pdf_uploads WHERE id=?", (upload_id,)).fetchone()
        if not latest or latest["state"] not in {"pending", "ready"} or latest["expires"] < time.time():
            raise HTTPException(409, "上传状态已改变，请重新打开文献。")
        if latest["state"] == "pending":
            conn.execute("UPDATE pdf_uploads SET state='retired' WHERE paper_id=? AND state='ready'", (paper_id,))
            conn.execute("UPDATE pdf_uploads SET state='ready',sha256=? WHERE id=?", (digest.hexdigest(), upload_id))
            conn.execute("UPDATE saved_papers SET has_pdf=1 WHERE id=? AND user_id=?", (paper_id, uid))
    return {"ok": True, "size": size, "sha256": digest.hexdigest()}
