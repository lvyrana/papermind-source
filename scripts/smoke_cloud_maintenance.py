"""Synthetic global cleanup, durable receipt and cloud timestamp HTTP check."""
import asyncio
from contextlib import closing
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit
import uuid

import httpx
from PIL import Image
from vercel.blob import put_blob,head_blob,BlobNotFoundError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'papermind'))
import auth
import cloud_files
import cloud_figures
import cloud_maintenance
from cloud_state import connection
from sql_backend import connect_remote,remote_credentials
from smoke_cloud_pdf import synthetic_pdf


def main(url):
    if not re.fullmatch(r'https://litfinch-migration-preview-[a-z0-9]+-lvyranas-projects.vercel.app',url):
        raise ValueError('Wrong preview URL')
    if os.environ.get('BLOB_STORE_ID') != 'store_RkRS2huB7kCab3j8': raise ValueError('Wrong storage')
    os.environ['PAPERMIND_SQL_BACKEND'] = 'turso'
    os.environ['PAPERMIND_FILE_BACKEND'] = 'vercel-blob'
    for role in ('library','auth','runtime'):
        host = urlsplit(remote_credentials(role)[0]).hostname or ''
        if not (host.startswith('litfinch-'+role+'-preview-') and host.endswith('.turso.io')): raise ValueError('Wrong database')
        with closing(connect_remote(role)) as conn:
            assert tuple(conn.execute('SELECT purpose,role FROM _papermind_preview_init').fetchone()) == ('litfinch-synthetic-business-v1',role)
    run = uuid.uuid4().hex
    users = ['00000000-0000-4000-8000-000000000001',str(uuid.uuid4())]
    tokens = []
    stage = 'seed'
    try:
        with auth.db() as conn:
            for index,uid in enumerate(users):
                account = 'synthetic-preview-owner' if index == 0 else 'synthetic-maintenance-'+run
                conn.execute('INSERT INTO accounts VALUES(?,?,?,?,?) ON CONFLICT(id) DO NOTHING',(account,account,'Synthetic maintenance',uid,int(time.time())))
                token = auth.new_session(conn,account)
                conn.execute('UPDATE sessions SET expires=? WHERE hash=?',(int(time.time())+1800,auth.digest(token)))
                tokens.append(token)
        with httpx.Client(base_url=url,headers={'x-vercel-trusted-oidc-idp-token':os.environ['VERCEL_OIDC_TOKEN']},timeout=60) as client:
            def request(method,path,data=None,who=0):
                return client.request(method,path,json=data,headers={'Authorization':'Bearer '+tokens[who]})
            assert request('POST','/api/maintenance/storage',{},who=1).status_code == 403
            response = request('POST','/api/library/save',{'paper':{'title':'Synthetic maintenance '+run,'summary_zh':'合成清理验证。'}},who=1)
            paper = response.json()['id']
            stage = 'timezone'
            saved = request('GET',f'/api/library/{paper}',who=1).json()['paper']['saved_at']
            parsed = datetime.fromisoformat(saved)
            assert parsed.tzinfo is not None and abs((datetime.now(timezone.utc)-parsed).total_seconds()) < 300
            stage = 'expired_synthetic_objects'
            pdf = synthetic_pdf(padding=32)
            png_buffer = io.BytesIO()
            Image.new('RGB',(8,8),'navy').save(png_buffer,format='PNG')
            png = png_buffer.getvalue()
            pdf_row = cloud_files.reserve(paper,users[1],len(pdf))
            fig_row = cloud_figures.reserve(paper,users[1],len(png),'image/png','results')
            # Direct fixture uploads only: no signed client permission exists.
            for row,content,mime in ((pdf_row,pdf,'application/pdf'),(fig_row,png,'image/png')):
                asyncio.run(put_blob(row['pathname'],content,access='private',content_type=mime,add_random_suffix=False,allow_overwrite=False,cache_control_max_age=60))
            with cloud_files.transaction() as conn:
                conn.execute('UPDATE pdf_uploads SET expires=0,created=0 WHERE id=?',(pdf_row['id'],))
                conn.execute('UPDATE figure_uploads SET expires=0,created=0 WHERE id=?',(fig_row['id'],))
            stage = 'shared_maintenance_lease'
            lease = cloud_maintenance.claim()
            assert lease
            try:
                assert request('POST','/api/maintenance/storage',{}).status_code == 409
            finally:
                with connection(write=True) as conn:
                    conn.execute("UPDATE maintenance_runs SET expires=0,lease=NULL WHERE name='storage' AND lease=?",(lease,))
            stage = 'global_cleanup_http'
            response = request('POST','/api/maintenance/storage',{})
            assert response.status_code == 200 and response.json()['ok'] and response.json()['reclaimed'] >= 2
            receipt = request('GET','/api/maintenance/storage').json()
            assert receipt['status'] == 'done' and receipt['summary'] == {key:response.json()[key] for key in ('groups','reclaimed','failed')}
            async def absent(path):
                try:
                    await head_blob(path)
                except BlobNotFoundError:
                    return True
                return False
            assert all(asyncio.run(absent(row['pathname'])) for row in (pdf_row,fig_row))
            print(json.dumps({'ok':True,'run_id':run,'checks':['owner_only','utc_offset_in_cloud_record','shared_lease_409','global_pdf_and_image_cleanup','durable_receipt','blob_absence_confirmed'],
                              'summary':receipt['summary'],'cron_schedule_active':False}))
        return 0
    except Exception as error:
        print(json.dumps({'ok':False,'stage':stage,'run_id':run,'error_type':type(error).__name__}))
        return 1
    finally:
        if tokens:
            with auth.db() as conn:
                for token in tokens: conn.execute('DELETE FROM sessions WHERE hash=?',(auth.digest(token),))
        print(json.dumps({'run_id':run,'synthetic_sessions_revoked':len(tokens)}))


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1]))
    except Exception:
        print(json.dumps({'ok':False,'stage':'target_or_cleanup'}))
        sys.exit(1)
