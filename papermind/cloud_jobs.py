"""Durable, owner-scoped work run within a bounded HTTP request (no daemon).

The browser starts queued work and resumes it on re-entry. An interrupted call
requires an explicit retry: provider execution/charging cannot be rolled back.
Job completion and its business result share the library database transaction.
"""
import asyncio
from contextlib import contextmanager
from time_utils import data_timestamp
import json
import os
import uuid

from fastapi import HTTPException
from src import database
from sql_backend import remote_enabled
from runtime_limits import RuntimeLimitError

SCHEMA = """
CREATE TABLE durable_jobs (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL,
 entity TEXT NOT NULL, state TEXT NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0, lease TEXT, expires INTEGER,
 created INTEGER NOT NULL, updated INTEGER NOT NULL, error TEXT NOT NULL DEFAULT '',
 UNIQUE(user_id,kind,entity),
 CHECK(kind IN ('paper-enrich','memory-core')),
 CHECK(state IN ('queued','running','done','failed','interrupted','cancelled')));
CREATE INDEX durable_jobs_owner ON durable_jobs(user_id,state,created);
CREATE TABLE _papermind_jobs_schema(version INTEGER PRIMARY KEY);
INSERT INTO _papermind_jobs_schema VALUES(1);
"""
LEASE_SECONDS = 120
CALL_SECONDS = 40
MAX_ATTEMPTS = 3


@contextmanager
def transaction():
    conn = database._ensure_db()
    try:
        conn.execute('BEGIN IMMEDIATE')
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


def _now(conn):
    return conn.execute("SELECT CAST(strftime('%s','now') AS INTEGER)").fetchone()[0]


def public(row):
    return {key: row[key] for key in ('id','kind','entity','state','attempts','error')}


def _owned_paper(conn, paper, uid):
    row = conn.execute('SELECT * FROM saved_papers WHERE id=? AND user_id=?', (paper, uid)).fetchone()
    if not row:
        raise HTTPException(404, 'not found')
    return dict(row)


def enqueue(uid, kind, entity):
    if not remote_enabled() or kind not in {'paper-enrich', 'memory-core'}:
        raise ValueError('Unsupported durable job')
    with transaction() as conn:
        if kind == 'paper-enrich':
            _owned_paper(conn, int(entity), uid)
        now = _now(conn)
        # Existing success/failure is preserved. Repeated saves do not spend
        # more money or reset the retry count.
        conn.execute("""INSERT INTO durable_jobs(id,user_id,kind,entity,state,created,updated)
                     VALUES(?,?,?,?,'queued',?,?) ON CONFLICT(user_id,kind,entity) DO NOTHING""",
                     (uuid.uuid4().hex, uid, kind, str(entity), now, now))
        return public(conn.execute('SELECT * FROM durable_jobs WHERE user_id=? AND kind=? AND entity=?',
                                   (uid, kind, str(entity))).fetchone())


def list_jobs(uid):
    with transaction() as conn:
        now = _now(conn)
        conn.execute("""UPDATE durable_jobs SET state='interrupted',lease=NULL,expires=NULL,
                     error='上次执行已中断，可重试。',updated=?
                     WHERE user_id=? AND state='running' AND expires<=?""", (now, uid, now))
        return [public(row) for row in conn.execute(
            "SELECT * FROM durable_jobs WHERE user_id=? AND state NOT IN ('done','cancelled') ORDER BY CASE state WHEN 'queued' THEN 0 WHEN 'running' THEN 1 ELSE 2 END,created LIMIT 20", (uid,)).fetchall()]


def retry(job_id, uid):
    with transaction() as conn:
        row = conn.execute('SELECT * FROM durable_jobs WHERE id=? AND user_id=?', (job_id, uid)).fetchone()
        if not row:
            raise HTTPException(404, 'not found')
        if row['state'] not in {'failed', 'interrupted'} or row['attempts'] >= MAX_ATTEMPTS:
            raise HTTPException(409, '该任务不能再次重试。')
        conn.execute("UPDATE durable_jobs SET state='queued',error='',updated=? WHERE id=? AND user_id=?", (_now(conn), job_id, uid))
    return {'ok': True}


def _claim(job_id, uid):
    with transaction() as conn:
        row = conn.execute('SELECT * FROM durable_jobs WHERE id=? AND user_id=?', (job_id, uid)).fetchone()
        if not row:
            raise HTTPException(404, 'not found')
        job = dict(row)
        if job['state'] != 'queued':
            return job, None
        if job['attempts'] >= MAX_ATTEMPTS:
            raise HTTPException(409, '任务已达到重试上限。')
        if job['kind'] == 'paper-enrich':
            inputs = _owned_paper(conn, int(job['entity']), uid)
        else:
            row = conn.execute('SELECT * FROM user_profiles WHERE user_id=?', (uid,)).fetchone()
            inputs = dict(row) if row else {}
        lease = uuid.uuid4().hex
        now = _now(conn)
        conn.execute("UPDATE durable_jobs SET state='running',attempts=attempts+1,lease=?,expires=?,updated=?,error='' WHERE id=?",
                     (lease, now + LEASE_SECONDS, now, job_id))
        job.update(state='running', attempts=job['attempts'] + 1, lease=lease)
        return job, inputs


async def _produce(job, inputs):
    # This switch is set only by the synthetic preview entrypoint. Real service
    # credentials are rejected there, and production cannot enable this mode.
    if os.environ.get('PAPERMIND_SYNTHETIC_TASKS') == 'true':
        if os.environ.get('VERCEL_ENV') != 'preview':
            raise RuntimeError('Synthetic jobs are preview-only')
        return ({'summary_zh': '合成验收摘要（未调用模型）。', 'relevance': '仅用于迁移验收。', 'category': '合成测试'}
                if job['kind'] == 'paper-enrich' else {'memory_core': '合成验收记忆（未调用模型）。'})
    if job['kind'] == 'memory-core':
        from memory_service import ensure_memory_core
        core, generated = await ensure_memory_core(job['user_id'], inputs, persist=False)
        return {'memory_core': core if generated else ''}
    if inputs.get('summary_zh') or not inputs.get('abstract'):
        return {}
    from llm_router import _llm_chat_complete_async
    from memory_service import build_memory_context
    context = build_memory_context(database.get_profile(job['user_id']), database.get_stated_memory(job['user_id']))
    prompt = ('根据以下论文标题和摘要生成中文解读，只返回 JSON，字段为 summary_zh（4–6句）、'
              'relevance（具体启发，80字内）、category（方法或设计标签，2–8字）。'
              '只依据提供的论文，不补造数字或结论；关联有限时如实说明。\n' + context +
              '\n标题：' + (inputs.get('title') or '')[:500] + '\n摘要：' + inputs['abstract'][:5000])
    raw, _, _ = await _llm_chat_complete_async([{'role': 'user', 'content': prompt}], max_tokens=1000, task='enrich')
    start, end = raw.find('{'), raw.rfind('}')
    result = json.loads(raw[start:end + 1])
    if not isinstance(result, dict) or not isinstance(result.get('summary_zh'), str) or not result['summary_zh'].strip():
        raise ValueError('No summary')
    return {key: str(result.get(key) or '')[:size] for key, size in (('summary_zh',4000),('relevance',800),('category',40))}


def _finish(job, uid, inputs, result=None, *, state='done', error=''):
    with transaction() as conn:
        current = conn.execute('SELECT * FROM durable_jobs WHERE id=? AND user_id=?', (job['id'], uid)).fetchone()
        now = _now(conn)
        if not current or current['state'] != 'running' or current['lease'] != job['lease'] or current['expires'] <= now:
            return {'id': job['id'], 'state': 'interrupted', 'error': '执行结果未写入，可刷新后重试。'}
        if result and state == 'done':
            if job['kind'] == 'paper-enrich':
                row = conn.execute('SELECT * FROM saved_papers WHERE id=? AND user_id=?', (int(job['entity']), uid)).fetchone()
                if not row or (row['title'],row['abstract'],row['summary_zh']) != (inputs['title'],inputs['abstract'],inputs['summary_zh']):
                    state = 'cancelled'
                else:
                    conn.execute('UPDATE saved_papers SET summary_zh=?,relevance=?,category=? WHERE id=? AND user_id=?',
                                 (result.get('summary_zh',''),result.get('relevance',''),result.get('category',''), int(job['entity']),uid))
            elif result.get('memory_core'):
                row = conn.execute('SELECT updated_at,memory_core FROM user_profiles WHERE user_id=?', (uid,)).fetchone()
                if not row or row['updated_at'] != inputs.get('updated_at') or row['memory_core']:
                    state = 'cancelled'
                else:
                    conn.execute("UPDATE user_profiles SET memory_core=?,core_source='auto_initial',last_core_merged_at=?,updated_at=? WHERE user_id=?",
                                 (result['memory_core'], data_timestamp(), data_timestamp(), uid))
        conn.execute('UPDATE durable_jobs SET state=?,error=?,lease=NULL,expires=NULL,updated=? WHERE id=? AND user_id=? AND lease=?',
                     (state, error, now, job['id'], uid, job['lease']))
        return public(conn.execute('SELECT * FROM durable_jobs WHERE id=?', (job['id'],)).fetchone())


async def run(job_id, uid):
    job, inputs = _claim(job_id, uid)
    if inputs is None:
        return {'ok': job['state'] == 'done', 'task': public(job)}
    try:
        result = await asyncio.wait_for(_produce(job, inputs), timeout=CALL_SECONDS)
        task = _finish(job, uid, inputs, result)
        return {'ok': task['state'] == 'done', 'task': task}
    except asyncio.CancelledError:
        _finish(job, uid, inputs, state='interrupted', error='执行已中断，可重试。')
        raise
    except RuntimeLimitError as error:
        task = _finish(job, uid, inputs, state='failed', error=str(error))
        return {'ok': False, 'task': task}
    except Exception:
        task = _finish(job, uid, inputs, state='failed', error='解读未完成，可重试。')
        return {'ok': False, 'task': task}
