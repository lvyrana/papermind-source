"""Private board images: direct uploads, atomic board entries and bounded exports."""
import asyncio
from time_utils import data_timestamp
import hashlib
import io
import json
import re
import time
import uuid

from fastapi import HTTPException
from src import database
from cloud_files import transaction, owned, check_quota, GC_GRACE, GC_RECHECK

MAX_IMAGE = 10 * 1024 * 1024
MAX_EXPORT_IMAGES = 40 * 1024 * 1024
SCHEMA = """
CREATE TABLE figure_uploads (
 id TEXT PRIMARY KEY,paper_id INTEGER NOT NULL,user_id TEXT NOT NULL,
 pathname TEXT UNIQUE NOT NULL,name TEXT UNIQUE NOT NULL,size INTEGER NOT NULL CHECK(size>0),
 mime TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('pending','ready','retired')),
 expires INTEGER NOT NULL,created INTEGER NOT NULL,sha256 TEXT,
 section TEXT NOT NULL,page INTEGER,caption TEXT NOT NULL,item_id INTEGER);
CREATE INDEX figure_uploads_owner ON figure_uploads(user_id,paper_id,state);
CREATE TABLE figure_cleanup(upload_id TEXT PRIMARY KEY,checked_at INTEGER NOT NULL);
CREATE TABLE _papermind_figure_schema(version INTEGER PRIMARY KEY);
INSERT INTO _papermind_figure_schema VALUES(1);
"""


def valid_path(row):
    ext = {'image/png':'png','image/jpeg':'jpg'}.get(row['mime'])
    return bool(ext and re.fullmatch('[a-f0-9]{32}', row['id'])
                and row['pathname'] == 'board-figure-v1/' + row['id'] + '.' + ext
                and row['name'] == str(row['paper_id']) + '-' + row['id'] + '.' + ext)


def reserve(paper, uid, size, mime, section, page=None, caption=''):
    if type(size) is not int or not 8 <= size <= MAX_IMAGE or mime not in {'image/png','image/jpeg'}:
        raise HTTPException(415, '仅支持不超过 10MB 的 PNG/JPEG。')
    if not section or len(section) > 40 or len(caption) > 8000 or (page is not None and (type(page) is not int or page < 1)):
        raise HTTPException(422, '图表信息无效。')
    with transaction() as conn:
        owned(conn,paper,uid)
        now = conn.execute("SELECT CAST(strftime('%s','now') AS INTEGER)").fetchone()[0]
        # Reissuing a grant for the same unfinished figure never reserves twice.
        row = conn.execute("SELECT * FROM figure_uploads WHERE paper_id=? AND user_id=? AND size=? AND mime=? AND section=? AND page IS ? AND caption=? AND state='pending' AND expires>? ORDER BY created DESC LIMIT 1",
                           (paper,uid,size,mime,section,page,caption,now)).fetchone()
        if row:
            return dict(row)
        check_quota(conn,uid,size)
        upload = uuid.uuid4().hex
        ext = 'png' if mime == 'image/png' else 'jpg'
        conn.execute("INSERT INTO figure_uploads VALUES(?,?,?,?,?,?,?,'pending',?,?,NULL,?,?,?,NULL)",
                     (upload,paper,uid,f'board-figure-v1/{upload}.{ext}',f'{paper}-{upload}.{ext}',size,mime,now+600,now,section,page,caption))
        return dict(conn.execute('SELECT * FROM figure_uploads WHERE id=?',(upload,)).fetchone())


def current(paper,uid,name):
    conn = database._ensure_db()
    try:
        owned(conn,paper,uid)
        row = conn.execute("SELECT f.* FROM figure_uploads f JOIN board_items b ON b.id=f.item_id AND b.paper_rowid=f.paper_id AND b.image=f.name WHERE f.paper_id=? AND f.user_id=? AND f.name=? AND f.state='ready'",(paper,uid,name)).fetchone()
        if not row or not valid_path(row):
            raise HTTPException(404,'图表不存在。')
        return dict(row)
    finally:
        conn.close()


async def read_bytes(row):
    from vercel.blob import stream_blob
    if not valid_path(row):
        raise HTTPException(404,'图表不存在。')
    data = bytearray()
    try:
        async with asyncio.timeout(20):
            async with stream_blob(row['pathname'],access='private') as source:
                if source.metadata.content_type != row['mime']:
                    raise HTTPException(415,'图片类型不匹配。')
                async for chunk in source:
                    data.extend(chunk)
                    if len(data) > min(row['size'],MAX_IMAGE):
                        raise HTTPException(413,'图片大小不匹配。')
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502,'图表暂时无法读取，请重试。') from None
    if len(data) != row['size'] or (row['sha256'] and hashlib.sha256(data).hexdigest() != row['sha256']):
        raise HTTPException(415,'图片大小或校验值不匹配。')
    return bytes(data)


async def complete(paper,uid,upload):
    with transaction() as conn:
        owned(conn,paper,uid)
        row = conn.execute('SELECT * FROM figure_uploads WHERE id=? AND paper_id=? AND user_id=?',(upload,paper,uid)).fetchone()
        if not row:
            raise HTTPException(404,'图表不存在。')
        row = dict(row)
        if row['state'] == 'ready':
            item = conn.execute('SELECT * FROM board_items WHERE id=? AND paper_rowid=?',(row['item_id'],paper)).fetchone()
            if item:
                return {'ok':True,'item':dict(item),'sha256':row['sha256']}
        if row['state'] != 'pending' or row['expires'] <= time.time():
            raise HTTPException(409,'上传已过期或被删除，请重新上传。')
    data = await read_bytes(row)
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            if image.format != ('PNG' if row['mime'] == 'image/png' else 'JPEG') or image.width*image.height > 40_000_000:
                raise ValueError('Invalid image')
            image.verify()
    except Exception:
        raise HTTPException(415,'图片内容无效。') from None
    digest = hashlib.sha256(data).hexdigest()
    with transaction() as conn:
        owned(conn,paper,uid)
        latest = conn.execute('SELECT state,expires,item_id FROM figure_uploads WHERE id=?',(upload,)).fetchone()
        if latest['state'] == 'ready':
            item = conn.execute('SELECT * FROM board_items WHERE id=?',(latest['item_id'],)).fetchone()
            if item:
                return {'ok':True,'item':dict(item),'sha256':digest}
        if latest['state'] != 'pending' or latest['expires'] <= time.time():
            raise HTTPException(409,'上传状态已改变。')
        now = data_timestamp()
        conn.execute('INSERT INTO presentation_boards(paper_rowid,sections_json,why_reading,created_at,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(paper_rowid) DO NOTHING',
                     (paper,json.dumps(database.DEFAULT_BOARD_SECTIONS,ensure_ascii=False),'',now,now))
        order = conn.execute('SELECT COALESCE(MAX(sort_order),0)+1 FROM board_items WHERE paper_rowid=? AND section=?',(paper,row['section'])).fetchone()[0]
        caption = row['caption'] or (f"图表（P.{row['page']}）" if row['page'] else '图表')
        cursor = conn.execute("INSERT INTO board_items(paper_rowid,section,content,quote,page,source,sort_order,image,created_at,updated_at) VALUES(?,?,?,'',?,'figure',?,?,?,?)",
                              (paper,row['section'],caption,row['page'],order,row['name'],now,now))
        item_id = cursor.lastrowid
        conn.execute("UPDATE figure_uploads SET state='ready',sha256=?,item_id=? WHERE id=?",(digest,item_id,upload))
        item = dict(conn.execute('SELECT * FROM board_items WHERE id=?',(item_id,)).fetchone())
    return {'ok':True,'item':item,'sha256':digest}


def retire_item(item_id,uid):
    with transaction() as conn:
        row = conn.execute('SELECT b.id FROM board_items b JOIN saved_papers p ON p.id=b.paper_rowid WHERE b.id=? AND p.user_id=?',(item_id,uid)).fetchone()
        if not row:
            raise HTTPException(404,'条目不存在。')
        conn.execute("UPDATE figure_uploads SET state='retired' WHERE item_id=? AND user_id=?",(item_id,uid))
        conn.execute('DELETE FROM board_items WHERE id=?',(item_id,))


async def cleanup(uid,limit=2):
    from vercel.blob import delete_blob, head_blob, BlobNotFoundError
    now = int(time.time())
    with transaction() as conn:
        rows = [dict(row) for row in conn.execute("SELECT f.* FROM figure_uploads f LEFT JOIN figure_cleanup g ON f.id=g.upload_id WHERE f.user_id=? AND f.state IN ('pending','retired') AND f.expires<? AND (g.upload_id IS NULL OR g.checked_at<?) ORDER BY (g.upload_id IS NOT NULL),f.created LIMIT ?",(uid,now-GC_GRACE,now-GC_RECHECK,max(1,min(limit,3)))).fetchall()]
        for row in rows:
            conn.execute("UPDATE figure_uploads SET state='retired' WHERE id=?",(row['id'],))
            conn.execute('DELETE FROM figure_cleanup WHERE upload_id=?',(row['id'],))
    reclaimed,failed = 0,0
    for row in rows:
        try:
            if not valid_path(row):
                raise ValueError('Invalid ledger path')
            async with asyncio.timeout(8):
                await delete_blob(row['pathname'])
                try:
                    await head_blob(row['pathname'])
                except BlobNotFoundError:
                    pass
                else:
                    raise ValueError('Deletion unconfirmed')
            with transaction() as conn:
                conn.execute('INSERT INTO figure_cleanup VALUES(?,?) ON CONFLICT(upload_id) DO UPDATE SET checked_at=excluded.checked_at',(row['id'],int(time.time())))
            reclaimed += 1
        except Exception:
            failed += 1
    return {'figures_reclaimed':reclaimed,'figures_cleanup_failed':failed}


def export_images(paper,uid,items):
    rows = {item['image']:current(paper,uid,item['image']) for item in items if item.get('image')}
    if sum(row['size'] for row in rows.values()) > MAX_EXPORT_IMAGES:
        raise HTTPException(413,'本次导出图表超过 40MB，请减少图表后再导出。')
    async def load():
        semaphore = asyncio.Semaphore(4)
        async def one(name,row):
            async with semaphore:
                return name,await read_bytes(row)
        try:
            async with asyncio.timeout(25):
                return dict(await asyncio.gather(*(one(name,row) for name,row in rows.items())))
        except TimeoutError:
            raise HTTPException(504,'读取导出图表超时，请重试。') from None
    # Export routes are synchronous FastAPI handlers, executing in a worker thread.
    return asyncio.run(load())
