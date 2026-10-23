"""Bounded global storage sweep for authenticated administration/daily cron."""
import asyncio
import json
import time
import uuid

from fastapi import HTTPException
from src import database
from sql_backend import remote_enabled
import cloud_files
import cloud_figures
from cloud_state import connection

SCHEMA = """
CREATE TABLE maintenance_runs(name TEXT PRIMARY KEY,lease TEXT,expires INTEGER NOT NULL,last_started INTEGER,last_finished INTEGER,status TEXT NOT NULL,summary TEXT NOT NULL);
CREATE TABLE _papermind_maintenance_schema(version INTEGER PRIMARY KEY);
INSERT INTO _papermind_maintenance_schema VALUES(1);
"""


def claim():
    with connection(write=True) as conn:
        now = conn.execute("SELECT CAST(strftime('%s','now') AS INTEGER)").fetchone()[0]
        row = conn.execute("SELECT expires FROM maintenance_runs WHERE name='storage'").fetchone()
        if row and row[0] > now:
            return None
        lease = uuid.uuid4().hex
        conn.execute("INSERT INTO maintenance_runs VALUES('storage',?,?,?,NULL,'running','{}') ON CONFLICT(name) DO UPDATE SET lease=excluded.lease,expires=excluded.expires,last_started=excluded.last_started,status='running'",
                     (lease,now+120,now))
        return lease


def candidates():
    now = int(time.time())
    conn = database._ensure_db()
    try:
        # Distinct owner/type pairs avoid two concurrent workers selecting the
        # same objects. Never-cleaned records take precedence over rechecks.
        return [tuple(row) for row in conn.execute("""
          SELECT user_id,kind FROM (
           SELECT p.user_id,'pdf' AS kind,MIN(g.upload_id IS NOT NULL) AS done,MIN(p.created) AS created
           FROM pdf_uploads p LEFT JOIN pdf_cleanup g ON p.id=g.upload_id
           WHERE p.state IN ('pending','retired') AND p.expires<? AND (g.upload_id IS NULL OR g.checked_at<?) GROUP BY p.user_id
           UNION ALL
           SELECT p.user_id,'figure' AS kind,MIN(g.upload_id IS NOT NULL) AS done,MIN(p.created) AS created
           FROM figure_uploads p LEFT JOIN figure_cleanup g ON p.id=g.upload_id
           WHERE p.state IN ('pending','retired') AND p.expires<? AND (g.upload_id IS NULL OR g.checked_at<?) GROUP BY p.user_id
          ) ORDER BY done,created LIMIT 3
        """,(now-cloud_files.GC_GRACE,now-cloud_files.GC_RECHECK,now-cloud_files.GC_GRACE,now-cloud_files.GC_RECHECK)).fetchall()]
    finally:
        conn.close()


async def run():
    if not remote_enabled() or not cloud_files.enabled():
        raise HTTPException(503,'云端维护未启用。')
    lease = claim()
    if lease is None:
        raise HTTPException(409,'文件维护正在运行。')
    summary = {'reclaimed':0,'failed':0,'groups':0}
    status = 'failed'
    try:
        groups = candidates()
        async def one(uid,kind):
            if kind == 'pdf':
                return await cloud_files.cleanup(uid,limit=3)
            result = await cloud_figures.cleanup(uid,limit=3)
            return {'reclaimed':result['figures_reclaimed'],'failed':result['figures_cleanup_failed']}
        async with asyncio.timeout(45):
            results = await asyncio.gather(*(one(uid,kind) for uid,kind in groups))
        summary = {'reclaimed':sum(item['reclaimed'] for item in results),'failed':sum(item['failed'] for item in results),'groups':len(groups)}
        status = 'done' if summary['failed'] == 0 else 'partial'
        return {'ok':status == 'done',**summary}
    finally:
        # An interrupted invocation stays conservative. A failed final write
        # leaves the lease until expiry instead of claiming a successful sweep.
        with connection(write=True) as conn:
            conn.execute("UPDATE maintenance_runs SET lease=NULL,expires=0,last_finished=CAST(strftime('%s','now') AS INTEGER),status=?,summary=? WHERE name='storage' AND lease=?",
                         (status,json.dumps(summary),lease))


def status():
    with connection() as conn:
        row = conn.execute("SELECT last_started,last_finished,status,summary FROM maintenance_runs WHERE name='storage'").fetchone()
        return {'last_started':row[0],'last_finished':row[1],'status':row[2],'summary':json.loads(row[3])} if row else {'status':'never_run'}
