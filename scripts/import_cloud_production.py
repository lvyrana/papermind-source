"""One-time, resumable import into explicitly pinned EMPTY production resources.

Prepare a fresh local copy with prepare_turso_snapshot first. No live source is
modified, no target table is dropped, and attachment bytes must read back intact.
Run before publishing the new site. A different snapshot needs new empty targets.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid
from contextlib import closing
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'papermind'))
from backup_data import digest, integrity
from backup_cloud import FILES, TABLES, SCHEMA_QUERY, capture, rows, pipeline
from sql_backend import remote_credentials

PROJECT = 'litfinch-production'
PROJECT_ID = 'prj_foZBNdOTd5MqHHR4ZHlKjdNTHSAs'
BLOB = 'store_aLYHu4co43NdzFzG'
MARKER = '_papermind_production_import'
PREFIXES = {'library':'litfinch-library-prod-', 'auth':'litfinch-auth-production-',
            'runtime':'litfinch-runtime-prod-'}


def validate_targets():
    if os.environ.get('BLOB_STORE_ID') != BLOB:
        raise ValueError('Wrong production Blob store')
    hosts = []
    for role, prefix in PREFIXES.items():
        parsed = urlsplit(remote_credentials(role)[0])
        if (parsed.scheme not in {'libsql','https'} or not parsed.hostname or
                not parsed.hostname.startswith(prefix) or not parsed.hostname.endswith('.turso.io') or
                parsed.username or parsed.password or parsed.port or parsed.path or parsed.query or parsed.fragment):
            raise ValueError('Wrong production database: '+role)
        hosts.append(parsed.hostname)
    if len(set(hosts)) != 3:
        raise ValueError('Production roles must be isolated')


def attachment_plan(data):
    """Stable IDs let interrupted uploads be verified and reused without overwrites."""
    receipt = json.loads((data/'PREPARATION-RECEIPT.json').read_text())
    if receipt.get('format') != 'papermind-turso-preparation-v1' or receipt.get('uploaded'):
        raise ValueError('Not an offline prepared snapshot')
    plan = []
    with closing(sqlite3.connect(data/'paperdiary.db')) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute('SELECT id,user_id FROM saved_papers WHERE has_pdf=1'):
            plan.append({'paper_id':row['id'],'user_id':row['user_id'], 'relative':f"pdfs/{row['id']}.pdf",'mime':'application/pdf'})
        for row in conn.execute("SELECT b.*,p.user_id FROM board_items b JOIN saved_papers p ON p.id=b.paper_rowid WHERE b.image!=''"):
            name = row['image']
            if not re.fullmatch(r'[A-Za-z0-9_.-]+',name):
                raise ValueError('Unsafe source image filename')
            from PIL import Image
            with Image.open(data/'figures'/name) as source:
                mime = {'PNG':'image/png','JPEG':'image/jpeg'}.get(source.format)
                if not mime or source.width*source.height > 40_000_000:
                    raise ValueError('Unsupported source image')
                source.verify()
            plan.append({'paper_id':row['paper_rowid'],'user_id':row['user_id'],
                         'relative':'figures/'+name,'mime':mime,'item_id':row['id'],
                         'section':row['section'],'page':row['page'],'caption':row['content']})
        if conn.execute("SELECT count(*) FROM board_items WHERE image!=''").fetchone()[0] != sum('item_id' in x for x in plan):
            raise ValueError('Image ownership mapping missing')
    for item in plan:
        path = data/item['relative']
        if path.is_symlink() or not path.is_file() or not item['user_id']:
            raise ValueError('Missing attachment or owner')
        item['size'], item['sha256'] = path.stat().st_size, digest(path)
        limit = 50*1024*1024 if item['mime']=='application/pdf' else 10*1024*1024
        if not 0 < item['size'] <= limit:
            raise ValueError('Attachment exceeds application limit')
        key = ':'.join([receipt['source_snapshot'], item['relative'], str(item.get('item_id','')),item['sha256']])
        item['id'] = uuid.uuid5(uuid.NAMESPACE_URL,key).hex
        ext = {'application/pdf':'pdf','image/png':'png','image/jpeg':'jpg'}[item['mime']]
        item['pathname'] = ('paper-pdf-v1/' if ext=='pdf' else 'board-figure-v1/')+item['id']+'.'+ext
        if ext != 'pdf': item['name'] = str(item['paper_id'])+'-'+item['id']+'.'+ext
    if len(plan)>100 or sum(x['size'] for x in plan)>200*1024*1024:
        raise ValueError('Source exceeds production file budget')
    for owner in {x['user_id'] for x in plan}:
        if sum(x['size'] for x in plan if x['user_id']==owner)>100*1024*1024:
            raise ValueError('Source exceeds per-user file budget')
    return plan


async def upload_files(data,plan):
    from vercel.blob import put_blob, head_blob, stream_blob, BlobNotFoundError
    semaphore = asyncio.Semaphore(3)
    async def one(item):
        async with semaphore:
            async with asyncio.timeout(180):
                try:
                    await head_blob(item['pathname'])
                except BlobNotFoundError:
                    await put_blob(item['pathname'], (data/item['relative']).read_bytes(), access='private',
                                   content_type=item['mime'], add_random_suffix=False, allow_overwrite=False)
                size, hasher = 0, hashlib.sha256()
                async with stream_blob(item['pathname'],access='private') as source:
                    if source.metadata.content_type != item['mime']:
                        raise ValueError('Uploaded MIME mismatch')
                    async for chunk in source:
                        size += len(chunk)
                        if size > item['size']: raise ValueError('Uploaded file grew')
                        hasher.update(chunk)
                if size != item['size'] or hasher.hexdigest()!=item['sha256']:
                    raise ValueError('Uploaded file hash mismatch')
        print(json.dumps({'stage':'attachment_verified','bytes':item['size']}),flush=True)
    await asyncio.gather(*(one(item) for item in plan))


def map_files(data,plan):
    with closing(sqlite3.connect(data/'paperdiary.db')) as conn:
        # The local copy is private and not used by a server. The source snapshot
        # remains untouched; its receipt is the authority for this import.
        for item in plan:
            if item['mime']=='application/pdf':
                conn.execute("INSERT INTO pdf_uploads VALUES(?,?,?,?,?,'ready',0,0,?)",
                             tuple(item[k] for k in ('id','paper_id','user_id','pathname','size','sha256')))
            else:
                conn.execute("INSERT INTO figure_uploads VALUES(?,?,?,?,?,?,?,'ready',0,0,?,?,?,?,?)",
                             tuple(item[k] for k in ('id','paper_id','user_id','pathname','name','size','mime','sha256','section','page','caption','item_id')))
                conn.execute('UPDATE board_items SET image=? WHERE id=? AND paper_rowid=?',
                             (item['name'],item['item_id'],item['paper_id']))
        conn.commit()


def row_hash(content):
    def encode(value):
        if isinstance(value,bytes): return {'bytes':value.hex()}
        raise TypeError('Unknown database value')
    normalized={name:sorted(json.dumps(row,ensure_ascii=False,default=encode) for row in values)
                for name,values in content.items()}
    return hashlib.sha256(json.dumps(normalized,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def local_export(path,role):
    integrity(path)
    with closing(sqlite3.connect(path)) as conn:
        schema = conn.execute(SCHEMA_QUERY).fetchall()
        tables = [name for kind,name,_,_ in schema if kind=='table']
        if not set(tables) <= TABLES[role] or MARKER in tables or '_papermind_preview_init' in tables:
            raise ValueError('Unexpected source tables')
        if conn.execute('SELECT * FROM _papermind_schema').fetchall()!=[(role,1)]:
            raise ValueError('Wrong source role')
        if any('AUTOINCREMENT' in sql.upper() for kind,_,_,sql in schema if kind=='table'):
            tables.append('sqlite_sequence')
        content = {table:[list(row) for row in conn.execute(f'SELECT * FROM "{table}"')] for table in tables}
        if conn.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Source has invalid foreign-key references')
        # SQLite's dump interleaves each table with its rows. Turso enforces
        # foreign keys, so a child insert can precede creation of its parent.
        # Create every table first, then load rows with deferred FK checking.
        dump = '\n'.join([sql+';' for kind,_,_,sql in schema if kind=='table']+
                         [line for line in conn.iterdump() if line.startswith(('INSERT INTO ','DELETE FROM '))]+
                         [sql+';' for kind,_,_,sql in schema if kind=='index'])
    if len(dump.encode())>8*1024*1024:
        raise ValueError('Import requires a larger-data migration strategy')
    return dump,content,row_hash(content)


def target_state(conn,role,fingerprint):
    tables=conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    if not tables: return 'empty'
    if MARKER not in {r[0] for r in tables}: raise ValueError('Target is not empty or marked')
    if conn.execute(f'SELECT role,fingerprint FROM {MARKER}').fetchall()!=[(role,fingerprint)]:
        raise ValueError('Target belongs to a different import')
    return 'imported'


def import_databases(data):
    import libsql
    exports={role:local_export(data/name,role) for role,name in FILES.items()}
    conns={}
    try:
        for role,(_,_,fingerprint) in exports.items():
            url,token=remote_credentials(role)
            conns[role]=libsql.connect(url,auth_token=token,timeout=30)
            target_state(conns[role],role,fingerprint)
        for role,(dump,content,fingerprint) in exports.items():
            conn=conns[role]
            if target_state(conn,role,fingerprint)=='empty':
                # One server-side transaction; schema/data/marker commit together.
                script=('BEGIN IMMEDIATE;\nPRAGMA defer_foreign_keys=ON;\n'+dump+f'\nCREATE TABLE {MARKER}(role TEXT PRIMARY KEY,fingerprint TEXT NOT NULL);\n'
                        +f"INSERT INTO {MARKER} VALUES('{role}','{fingerprint}');\nCOMMIT;")
                # libsql 0.1.11 Connection.executescript discards its Result.
                # Cursor.executescript propagates server errors; never infer
                # success from the connection method's unconditional None.
                try: conn.cursor().executescript(script)
                except Exception:
                    try: conn.rollback()
                    except Exception: pass
                    raise
            received=capture(role)['data']
            received.pop(MARKER,None)
            # Ordinary backups intentionally omit empty session/config tables.
            from backup_cloud import EMPTY
            expected={k:v for k,v in content.items() if k not in EMPTY[role]}
            if row_hash(received)!=row_hash(expected): raise ValueError('Imported data checksum differs: '+role)
            print(json.dumps({'stage':'database_verified','role':role,'tables':len(content),'rows':sum(map(len,content.values()))}),flush=True)
    finally:
        for conn in conns.values(): conn.close()
    return {role:{'sha256':value[2],'rows':{name:len(rows) for name,rows in value[1].items()}} for role,value in exports.items()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    data=args.data.resolve()
    saved_plan=data/'CLOUD-FILE-PLAN.json'
    if saved_plan.exists(): plan=json.loads(saved_plan.read_text())
    else:
        plan=attachment_plan(data)
        for role,name in FILES.items(): local_export(data/name,role)
    if not args.apply:
        print(json.dumps({'attachments':len(plan),'bytes':sum(x['size'] for x in plan),'remote_writes':False}))
        return
    validate_targets()
    # Refuse any unrelated populated target before uploading the first object.
    for role in FILES:
        schema=rows(pipeline(role,[SCHEMA_QUERY])[0])
        if schema and MARKER not in {row[1] for row in schema}:
            raise ValueError('Nonempty production target')
    if not saved_plan.exists():
        asyncio.run(upload_files(data,plan))
        # Keep a private stable plan before a later network retry. Local changes
        # are a single transaction, so interrupted mapping fails closed.
        map_files(data,plan)
        with saved_plan.open('x') as stream: json.dump(plan,stream)
        saved_plan.chmod(0o600)
    databases=import_databases(data)
    receipt={'project':PROJECT,'project_id':PROJECT_ID,'blob_store':BLOB,'databases':databases,
             'files':len(plan),'bytes':sum(x['size'] for x in plan),'verified_at':int(time.time())}
    target=data/'CLOUD-IMPORT-RECEIPT.json'
    target.write_text(json.dumps(receipt,indent=2)+'\n'); target.chmod(0o600)
    print(json.dumps({'ok':True,'files':len(plan),'receipt':str(target)}))


if __name__=='__main__':
    try: main()
    except Exception as error:
        # Never dump driver errors, row contents, paths or bearer credentials.
        print(json.dumps({'ok':False,'error_type':type(error).__name__,'stage':'production_import'}))
        sys.exit(1)
