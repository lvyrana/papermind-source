"""Synthetic private PNG >4.5MB, board exports and real browser snipping."""
import asyncio
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit
import uuid
from zipfile import ZipFile

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'papermind'))
import auth
import cloud_files
from sql_backend import connect_remote, remote_credentials
from smoke_cloud_pdf import synthetic_pdf
from vercel.blob import put_blob


def main(url, browser=False):
    if not re.fullmatch(r'https://litfinch-migration-preview-[a-z0-9]+-lvyranas-projects.vercel.app',url):
        raise ValueError('Wrong preview URL')
    if os.environ.get('BLOB_STORE_ID') != 'store_RkRS2huB7kCab3j8':
        raise ValueError('Wrong test storage')
    os.environ['PAPERMIND_SQL_BACKEND'] = 'turso'
    for role in ('library','auth'):
        host = urlsplit(remote_credentials(role)[0]).hostname or ''
        if not (host.startswith('litfinch-'+role+'-preview-') and host.endswith('.turso.io')):
            raise ValueError('Wrong test database')
        with closing(connect_remote(role)) as conn:
            assert tuple(conn.execute('SELECT purpose,role FROM _papermind_preview_init').fetchone()) == ('litfinch-synthetic-business-v1',role)
    run = uuid.uuid4().hex
    tokens = []
    users = [str(uuid.uuid4()),str(uuid.uuid4())]
    stage = 'seed'
    try:
        with auth.db() as conn:
            for index,uid in enumerate(users):
                account = f'synthetic-figures-{run}-{index}'
                conn.execute('INSERT INTO accounts VALUES(?,?,?,?,?)',(account,account,'Synthetic figures',uid,int(time.time())))
                token = auth.new_session(conn,account)
                conn.execute('UPDATE sessions SET expires=? WHERE hash=?',(int(time.time())+1800,auth.digest(token)))
                tokens.append(token)
        with httpx.Client(base_url=url,headers={'x-vercel-trusted-oidc-idp-token':os.environ['VERCEL_OIDC_TOKEN']},timeout=60) as client:
            def request(method,path,data=None,who=0):
                return client.request(method,path,json=data,headers={'Authorization':'Bearer '+tokens[who]})
            result = request('POST','/api/library/save',{'paper':{'title':'Synthetic figures '+run,'summary_zh':'合成文件验收。','source':'local_pdf'}})
            assert result.status_code == 200
            paper = result.json()['id']
            if browser != 'only':
                stage = 'large_image_signed_upload'
                buffer = io.BytesIO()
                Image.frombytes('RGB',(1536,1366),os.urandom(1536*1366*3)).save(buffer,format='PNG',compress_level=0)
                png = buffer.getvalue()
                assert 4.5*1024*1024 < len(png) < 10*1024*1024
                payload = {'kind':'figure','paper_id':paper,'size':len(png),'mime':'image/png','section':'results','page':1,'caption':'合成迁移图表'}
                assert request('POST','/api/file-grant',payload,who=1).status_code == 404
                grant = request('POST','/api/file-grant',payload)
                assert grant.status_code == 200
                parsed = urlsplit(grant.json()['upload_url'])
                assert parsed.scheme == 'https' and parsed.netloc == 'vercel.com' and parsed.path == '/api/blob/'
                with httpx.Client(timeout=90) as anonymous:
                    assert anonymous.put(grant.json()['upload_url'],content=png,headers={'Content-Type':'image/png'}).status_code == 200
                upload = grant.json()['upload_id']
                result = request('POST',f'/api/board/{paper}/figure-upload/{upload}')
                assert result.status_code == 200
                item = result.json()['item']
                assert result.json()['sha256'] == hashlib.sha256(png).hexdigest()
                assert request('POST',f'/api/board/{paper}/figure-upload/{upload}').json()['item']['id'] == item['id']
                stage = 'download_and_exports'
                path = f"/api/board/{paper}/figures/{item['image']}"
                downloaded = request('GET',path)
                assert downloaded.status_code == 200 and downloaded.content == png
                assert request('GET',path,who=1).status_code == 404
                assert request('GET',f'/api/board/{paper}/export/pptx',who=1).status_code == 404
                marp = request('GET',f'/api/board/{paper}/export/marp')
                assert marp.status_code == 200 and 'data:image/png;base64,' in marp.text and len(marp.content)>4.5*1024*1024
                pptx = request('GET',f'/api/board/{paper}/export/pptx')
                assert pptx.status_code == 200 and len(pptx.content)>4.5*1024*1024
                with ZipFile(io.BytesIO(pptx.content)) as archive:
                    images = [name for name in archive.namelist() if name.startswith('ppt/media/')]
                    assert len(images) == 1 and archive.read(images[0]) == png
                stage = 'delete_revokes_access'
                assert request('DELETE',f"/api/board/items/{item['id']}",who=1).status_code == 404
                assert request('DELETE',f"/api/board/items/{item['id']}").status_code == 200
                assert request('GET',path).status_code == 404
                assert request('POST',f'/api/board/{paper}/figure-upload/{upload}').status_code == 409
                # Keep the just-issued grant's object until expiry+grace. Do not
                # backdate a real still-valid signed capability for faster tests.
                print(json.dumps({'ok':True,'stage':'image_http_complete','run_id':run,'image_bytes':len(png),'pptx_bytes':len(pptx.content),
                    'checks':['signed_direct_upload','verified_image','idempotent_completion','private_read','other_account_denied','large_streamed_exports','image_embedded_in_pptx','delete_revokes_read'],
                    'retired_object_retained_for_grace':True}),flush=True)
            if browser:
                stage = 'browser_snip'
                pdf = synthetic_pdf(padding=32)
                row = cloud_files.reserve(paper,users[0],len(pdf))
                asyncio.run(put_blob(row['pathname'],pdf,access='private',content_type='application/pdf',add_random_suffix=False,allow_overwrite=False,cache_control_max_age=60))
                assert request('POST',f"/api/library/{paper}/pdf-upload/{row['id']}").status_code == 200
                env = {**os.environ,'PAPERMIND_TEST_URL':url,'PAPERMIND_TEST_PAPER':str(paper),
                       'PAPERMIND_TEST_SESSION':tokens[0],'PAPERMIND_TEST_UID':users[0]}
                assert subprocess.run(['node','--use-env-proxy',str(ROOT/'web/scripts/check-cloud-figures.mjs')],env=env).returncode == 0
            print(json.dumps({'ok':True,'run_id':run,'paper_id':paper,'real_model_calls':0}))
        return 0
    except Exception as error:
        print(json.dumps({'ok':False,'stage':stage,'run_id':run,'error_type':type(error).__name__}))
        return 1
    finally:
        if tokens:
            with auth.db() as conn:
                for token in tokens:
                    conn.execute('DELETE FROM sessions WHERE hash=?',(auth.digest(token),))
        print(json.dumps({'run_id':run,'synthetic_sessions_revoked':len(tokens)}))


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1], 'only' if '--browser-only' in sys.argv else '--browser' in sys.argv))
    except Exception:
        print(json.dumps({'ok':False,'stage':'target_or_cleanup'}))
        sys.exit(1)
