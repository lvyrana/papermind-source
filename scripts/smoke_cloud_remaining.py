"""One synthetic HTTP pass for remaining persistent reading routes; no model calls."""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit
import uuid
import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'papermind'))
import auth
from sql_backend import connect_remote,remote_credentials


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
    users = [str(uuid.uuid4()),str(uuid.uuid4())]
    tokens = []
    stage = 'seed'
    checks = []
    try:
        with auth.db() as conn:
            for index,uid in enumerate(users):
                account = 'synthetic-remaining-'+run+'-'+str(index)
                conn.execute('INSERT INTO accounts VALUES(?,?,?,?,?)',(account,account,'Synthetic remaining',uid,int(time.time())))
                token = auth.new_session(conn,account)
                conn.execute('UPDATE sessions SET expires=? WHERE hash=?',(int(time.time())+1800,auth.digest(token)))
                tokens.append(token)
        with httpx.Client(base_url=url,headers={'x-vercel-trusted-oidc-idp-token':os.environ['VERCEL_OIDC_TOKEN']},timeout=60) as client:
            def request(method,path,data=None,who=0,origin=None):
                response = client.request(method,path,json=data,headers={'Authorization':'Bearer '+tokens[who],**({'Origin':origin} if origin else {})})
                response.raise_for_status()
                return response
            title = 'Synthetic remaining '+run
            paper = request('POST','/api/library/save',{'paper':{'title':title,'summary_zh':'合成接口验证。','authors':'Synthetic Author','journal':'Synthetic Journal','pub_date':'2026'}}).json()['id']
            stage = 'quotes'
            quote = request('POST','/api/quotes',{'paper_rowid':paper,'text':'合成摘录 '+run,'page':1}).json()['quote']
            assert request('GET',f'/api/quotes/{paper}').json()['quotes'][0]['text'] == quote['text']
            assert request('GET',f'/api/quotes/{paper}',who=1).json()['quotes'] == []
            assert not request('DELETE',f"/api/quotes/{quote['id']}",who=1).json()['ok']
            checks.append('quote_readback_and_owner_isolation')
            stage = 'reading_memory'
            assert request('POST','/api/reading-history',{'paper_rowid':paper,'title':title}).json()['ok']
            assert title in request('GET','/api/reading-history').text
            assert title not in request('GET','/api/reading-history',who=1).text
            memory = '合成人工画像 '+run
            assert request('PATCH','/api/memory',{'memory_core':memory}).json()['memory_core'] == memory
            assert request('GET','/api/memory').json()['memory_core'] == memory
            assert request('GET','/api/memory',who=1).json()['memory_core'] != memory
            assert request('GET',f'/api/self-test/{paper}').json()['ok']
            assert request('GET',f'/api/papers/{paper}/ocr-pages').status_code == 200
            checks.append('history_manual_memory_selftest_ocr_read')
            stage = 'exports'
            note = '合成笔记 '+run
            assert request('POST','/api/notes',{'paper_rowid':paper,'content':note}).json()['ok']
            assert note in request('GET','/api/export/notes-markdown').text
            assert note not in request('GET','/api/export/notes-markdown',who=1).text
            for extension in ('ris','bibtex'):
                assert title in request('GET',f'/api/export/{extension}/{paper}').text
                assert title in request('POST',f'/api/export/{extension}-direct',{'paper':{'title':title}}).text
            assert request('POST','/api/feedback',{'type':'synthetic-test','content':'SYNTHETIC ONLY '+run}).json()['ok']
            checks.append('notes_and_citation_exports_feedback')
            stage = 'grant'
            result = request('POST','/api/file-grant',{'kind':'figure','paper_id':paper,'size':80,'mime':'image/png','section':'results'},origin=url).json()
            assert re.fullmatch(r'[0-9a-f]{32}',result['upload_id'])
            assert urlsplit(result['upload_url']).hostname == 'vercel.com'
            # No PUT: this checks the final Node grant only. Large file/image
            # storage and browser flows already passed in the earlier batches.
            checks.append('final_node_image_grant')
            stage = 'delete_synthetic_quote'
            assert request('DELETE',f"/api/quotes/{quote['id']}").json()['ok']
            assert request('GET',f'/api/quotes/{paper}').json()['quotes'] == []
            print(json.dumps({'ok':True,'run_id':run,'paper_id':paper,'checks':checks,'real_model_calls':0}))
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
    try: sys.exit(main(sys.argv[1]))
    except Exception:
        print(json.dumps({'ok':False,'stage':'target_or_cleanup'}))
        sys.exit(1)
