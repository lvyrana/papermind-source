"""Export cloud data and private attachments to a verified, local recovery snapshot.

No remote writes. Configuration, invitations, sessions and live leases are not
fetched. Re-read the exported data after copying immutable attachments; any
observed change aborts publication. Production cutover still requires stop-write.
"""
import argparse
import asyncio
import base64
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'papermind'))
from sql_backend import remote_credentials
from backup_data import create, verify, restore, digest
from cloud_figures import valid_path

TABLES = {
 'library': set(('saved_papers paper_notes paper_chats reading_history user_profiles rate_limits search_runs enrichment_cache user_feedback projects reading_cards paper_quotes presentation_boards board_items self_test_sessions paper_page_ocr method_gaps pdf_uploads pdf_cleanup figure_uploads figure_cleanup durable_jobs _papermind_schema _papermind_preview_init _papermind_file_schema _papermind_figure_schema _papermind_jobs_schema').split()),
 'auth': set(('accounts sessions flows devices attempts _papermind_schema _papermind_preview_init').split()),
 'runtime': set(('attempts ai_policy ai_leases app_config trial_invitations provider_cooldowns maintenance_runs _papermind_schema _papermind_preview_init _papermind_runtime_schema _papermind_state_schema _papermind_maintenance_schema').split()),
}
EMPTY = {'library':set(), 'auth':{'sessions','flows','devices','attempts'},
         'runtime':{'app_config','trial_invitations','provider_cooldowns','ai_leases','maintenance_runs'}}
FILES = {'library':'paperdiary.db','auth':'auth.sqlite3','runtime':'runtime-limits/daily.sqlite3'}
for allowed in TABLES.values():
    allowed.add('_papermind_production_import')
SCHEMA_QUERY = "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END,name"


def decode(cell):
    kind = cell['type']
    if kind == 'null': return None
    if kind == 'integer': return int(cell['value'])
    if kind == 'float': return float(cell['value'])
    if kind == 'text': return cell['value']
    if kind == 'blob': return base64.b64decode(cell['base64'],validate=True)
    raise ValueError('Unknown SQL data type')


def pipeline(role, statements):
    url,token = remote_credentials(role)
    parsed = urlsplit(url)
    if not (parsed.scheme in {'libsql','https'} and parsed.hostname and parsed.hostname.endswith('.turso.io') and
            not (parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment) and parsed.path in {'','/'}):
        raise ValueError('Invalid database host')
    payload = {'requests':[{'type':'execute','stmt':{'sql':sql}} for sql in statements]+[{'type':'close'}]}
    # One request keeps the read transaction inside Turso's short transaction
    # window. Do not follow redirects with a database bearer credential.
    with httpx.Client(timeout=30,follow_redirects=False) as client:
        with client.stream('POST','https://'+parsed.hostname+'/v2/pipeline',headers={'Authorization':'Bearer '+token},json=payload) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 32*1024*1024:
                    raise ValueError('Backup response exceeds bounded export size')
    results = json.loads(data)['results']
    if len(results) != len(statements)+1 or any(item['type'] != 'ok' for item in results):
        raise ValueError('Read-only snapshot query failed')
    return [item['response']['result'] for item in results[:-1]]


def rows(result):
    return [[decode(cell) for cell in row] for row in result['rows']]


def capture(role):
    schema = rows(pipeline(role,[SCHEMA_QUERY])[0])
    tables = [name for kind,name,table,sql in schema if kind == 'table']
    if not tables or not set(tables) <= TABLES[role] or '_papermind_schema' not in tables:
        raise ValueError('Unknown backup schema')
    for kind,name,table,sql in schema:
        if kind not in {'table','index'} or table not in TABLES[role] or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',name):
            raise ValueError('Unexpected schema object')
    exported = [table for table in tables if table not in EMPTY[role]]
    if any('AUTOINCREMENT' in sql.upper() for kind,name,table,sql in schema if kind == 'table'):
        exported.append('sqlite_sequence')
    result = pipeline(role,['BEGIN DEFERRED',SCHEMA_QUERY]+[f'SELECT * FROM "{table}"' for table in exported]+['ROLLBACK'])
    if rows(result[1]) != schema:
        raise ValueError('Schema changed during snapshot')
    content = {table:rows(data) for table,data in zip(exported,result[2:-1])}
    if content.get('_papermind_schema') != [[role,1]]:
        raise ValueError('Wrong database role')
    return {'schema':schema,'data':content}


def fingerprint(snapshot):
    def encode(value):
        return {'blob':base64.b64encode(value).decode()} if isinstance(value,bytes) else value
    normalized = {'schema':snapshot['schema'],'data':{name:sorted(json.dumps(row,default=encode,ensure_ascii=False) for row in values)
                  for name,values in snapshot['data'].items()}}
    return hashlib.sha256(json.dumps(normalized,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def write_database(path,snapshot):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd = os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(fd)
    with closing(sqlite3.connect(path)) as conn:
        # Database schema is data from a remote source, not permission to access
        # local files or load extensions. Only table/index definitions accepted.
        forbidden = {sqlite3.SQLITE_ATTACH,sqlite3.SQLITE_DETACH,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_PRAGMA,
                     sqlite3.SQLITE_CREATE_TRIGGER,sqlite3.SQLITE_CREATE_VIEW,sqlite3.SQLITE_CREATE_VTABLE}
        conn.set_authorizer(lambda action,*_: sqlite3.SQLITE_DENY if action in forbidden else sqlite3.SQLITE_OK)
        for kind,name,table,sql in snapshot['schema']:
            if kind == 'table': conn.execute(sql)
        for table,values in snapshot['data'].items():
            if table == 'sqlite_sequence':
                # Preserve the high-water mark even when the largest ID was
                # deleted; otherwise restored IDs could collide with tombstones.
                conn.execute('DELETE FROM sqlite_sequence')
            if values:
                conn.executemany(f'INSERT INTO "{table}" VALUES('+','.join('?' for _ in values[0])+')',values)
        for kind,name,table,sql in snapshot['schema']:
            if kind == 'index': conn.execute(sql)
        conn.commit()
        conn.set_authorizer(None)
        if 'durable_jobs' in snapshot['data']:
            conn.execute("UPDATE durable_jobs SET state='interrupted',lease=NULL,expires=NULL,error='从备份恢复，请确认后重试。' WHERE state IN ('queued','running')")
            conn.commit()
        assert conn.execute('PRAGMA integrity_check').fetchall() == [('ok',)]


async def copy_attachments(data):
    from vercel.blob import stream_blob
    with closing(sqlite3.connect(data/'paperdiary.db')) as conn:
        conn.row_factory = sqlite3.Row
        records = []
        for row in conn.execute("SELECT p.* FROM pdf_uploads p JOIN saved_papers s ON s.id=p.paper_id AND s.user_id=p.user_id WHERE p.state='ready' AND s.has_pdf=1"):
            row = dict(row)
            if not re.fullmatch('[a-f0-9]{32}',row['id']) or row['pathname'] != 'paper-pdf-v1/'+row['id']+'.pdf':
                raise ValueError('Invalid PDF ledger')
            records.append((row,'pdfs/'+str(row['paper_id'])+'.pdf'))
        for row in conn.execute("SELECT f.* FROM figure_uploads f JOIN board_items b ON b.id=f.item_id AND b.image=f.name JOIN saved_papers s ON s.id=f.paper_id AND s.user_id=f.user_id WHERE f.state='ready'"):
            row = dict(row)
            if not valid_path(row): raise ValueError('Invalid image ledger')
            records.append((row,'figures/'+row['name']))
        if conn.execute('SELECT count(*) FROM saved_papers WHERE has_pdf=1').fetchone()[0] != sum(name.startswith('pdfs/') for _,name in records):
            raise ValueError('A saved PDF has no recoverable ledger')
        if conn.execute("SELECT count(*) FROM board_items WHERE image!=''").fetchone()[0] != sum(name.startswith('figures/') for _,name in records):
            raise ValueError('A board image has no recoverable ledger')
    if sum(row['size'] for row,_ in records) > 200*1024*1024:
        raise ValueError('Attachment backup exceeds current application budget')
    for row,name in records:
        target = data/name
        target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        expected,hasher,size = row['sha256'],hashlib.sha256(),0
        if not expected: raise ValueError('Unverified attachment')
        fd = os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'wb') as stream:
            async with asyncio.timeout(60):
                async with stream_blob(row['pathname'],access='private') as source:
                    async for chunk in source:
                        size += len(chunk)
                        if size > row['size']: raise ValueError('Attachment grew')
                        stream.write(chunk)
                        hasher.update(chunk)
        if size != row['size'] or hasher.hexdigest() != expected:
            raise ValueError('Attachment checksum mismatch')
    return len(records)


def backup(destination):
    captured = {role:capture(role) for role in FILES}
    with tempfile.TemporaryDirectory(prefix='litfinch-cloud-recovery-') as temporary:
        data = Path(temporary)/'data'
        data.mkdir(mode=0o700)
        for role,name in FILES.items():
            write_database(data/name,captured[role])
        count = asyncio.run(copy_attachments(data))
        if any(fingerprint(capture(role)) != fingerprint(captured[role]) for role in FILES):
            raise ValueError('Data changed during backup; retry in a quiet window')
        snapshot = create(data,destination)
        manifest = verify(snapshot)
        # Extend the verified manifest with explicit exclusions and consistency.
        manifest.update(cloud_source=True,cloud_attachments=count,
                        consistency='transactional per-role reads, identical exported rows after attachment copy; not a cross-database transaction',
                        excluded=['app_config','trial_invitations','provider_cooldowns','ai_leases','maintenance_runs','auth sessions/flows/devices/attempts','environment credentials'])
        (snapshot/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        verify(snapshot)
        return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',choices=['litfinch-migration-preview','litfinch-production'],required=True)
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--restore-check',type=Path,required=True)
    args = parser.parse_args()
    if args.restore_check.exists(): raise ValueError('Restore path must be new')
    if args.project == 'litfinch-production':
        from import_cloud_production import validate_targets
        validate_targets()
    else:
        if os.environ.get('BLOB_STORE_ID') != 'store_RkRS2huB7kCab3j8': raise ValueError('Wrong preview storage')
        for role in FILES:
            host = urlsplit(remote_credentials(role)[0]).hostname or ''
            if not (host.startswith('litfinch-'+role+'-preview-') and host.endswith('.turso.io')):
                raise ValueError('Wrong preview database')
    snapshot = backup(args.destination)
    restored = restore(snapshot,args.restore_check)
    with closing(sqlite3.connect(restored/'auth.sqlite3')) as conn:
        assert conn.execute('SELECT count(*) FROM sessions').fetchone()[0] == 0
        accounts = conn.execute('SELECT count(*) FROM accounts').fetchone()[0]
    with closing(sqlite3.connect(restored/'runtime-limits/daily.sqlite3')) as conn:
        assert conn.execute('SELECT count(*) FROM app_config').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM trial_invitations').fetchone()[0] == 0
    manifest = verify(snapshot)
    print(json.dumps({'ok':True,'snapshot':str(snapshot),'restored_to':str(restored),'files':len(manifest['files']),
                      'attachments':manifest['cloud_attachments'],'accounts_preserved':accounts,'sessions_and_config_excluded':True,'remote_writes':False}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'ok':False,'stage':'cloud_backup_or_restore','error_type':type(error).__name__}))
        sys.exit(1)
