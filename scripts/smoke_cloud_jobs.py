"""Synthetic HTTP tasks/configuration acceptance; no real OAuth or model calls."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'papermind'))
import auth
import cloud_jobs as jobs
import cloud_state as state
import trial_access
from sql_backend import connect_remote, remote_credentials

OWNER = '00000000-0000-4000-8000-000000000001'


def main(url, browser=False):
    if not re.fullmatch(r'https://litfinch-migration-preview-[a-z0-9]+-lvyranas-projects.vercel.app', url):
        raise ValueError('Wrong preview URL')
    os.environ['PAPERMIND_SQL_BACKEND'] = 'turso'
    for role in ('library','auth','runtime'):
        host = urlsplit(remote_credentials(role)[0]).hostname or ''
        if not (host.startswith('litfinch-' + role + '-preview-') and host.endswith('.turso.io')):
            raise ValueError('Wrong test database')
        with closing(connect_remote(role)) as conn:
            assert [tuple(row) for row in conn.execute('SELECT purpose,role FROM _papermind_preview_init').fetchall()] == [('litfinch-synthetic-business-v1',role)]
    run = uuid.uuid4().hex
    users = [str(uuid.uuid4()), str(uuid.uuid4()), OWNER]
    tokens = []
    previous = None
    stage = 'seed'
    try:
        with auth.db() as conn:
            for index, uid in enumerate(users):
                account = 'synthetic-jobs-' + run + '-' + str(index) if index < 2 else 'synthetic-preview-owner'
                conn.execute('INSERT INTO accounts VALUES(?,?,?,?,?) ON CONFLICT(id) DO NOTHING',
                             (account,account,'Synthetic tasks',uid,int(time.time())))
                token = auth.new_session(conn, account)
                conn.execute('UPDATE sessions SET expires=? WHERE hash=?',(int(time.time())+1800, auth.digest(token)))
                tokens.append(token)
        base_headers = {'x-vercel-trusted-oidc-idp-token':os.environ['VERCEL_OIDC_TOKEN']}
        with httpx.Client(base_url=url,headers=base_headers,timeout=50) as client:
            def request(path, method='GET', who=0, data=None, headers=None):
                supplied = {'Authorization':'Bearer '+tokens[who]} if who is not None else {}
                return client.request(method,path,json=data,headers={**supplied,**(headers or {})})
            def save(suffix):
                response = request('/api/library/save','POST',data={'paper':{'title':'Synthetic job '+run+suffix,
                    'doi':'10.test/'+run+suffix,'abstract':'Synthetic migration acceptance only.'}})
                assert response.status_code == 200
                data = response.json()
                assert data['task']['state'] == 'queued'
                return data
            if browser == 'only':
                stage = 'browser_reentry'
                third = save('-browser')
                env = {**os.environ,'PAPERMIND_TEST_URL':url,'PAPERMIND_TEST_SESSION':tokens[0],
                       'PAPERMIND_TEST_PAPER':str(third['id']),'PAPERMIND_TEST_JOB':third['task']['id'],
                       'PAPERMIND_TEST_UID':users[0]}
                return subprocess.run(['node','--use-env-proxy',str(ROOT/'web/scripts/check-cloud-jobs.mjs')],env=env).returncode
            stage = 'durable_queue_and_account_isolation'
            first = save('-first')
            job = first['task']['id']
            assert save('-first')['task']['id'] == job
            with httpx.Client(base_url=url,headers={**base_headers,'Authorization':'Bearer '+tokens[0]},timeout=50) as fresh:
                assert any(item['id'] == job for item in fresh.get('/api/tasks').json()['tasks'])
            assert request('/api/tasks/'+job+'/run','POST',who=1,data={}).status_code == 404
            assert request('/api/tasks',who=1).json()['tasks'] == []
            assert request('/api/tasks',who=None,headers={'X-User-ID':users[0]}).status_code == 401
            stage = 'concurrent_execution_once'
            with ThreadPoolExecutor(2) as pool:
                responses = list(pool.map(lambda _: request('/api/tasks/'+job+'/run','POST',data={}),range(2)))
            assert all(response.status_code == 200 for response in responses)
            assert any(response.json()['ok'] for response in responses)
            assert '合成验收摘要' in request('/api/library/'+str(first['id'])).json()['paper']['summary_zh']
            with closing(connect_remote('library')) as conn:
                assert tuple(conn.execute('SELECT state,attempts FROM durable_jobs WHERE id=?',(job,)).fetchone()) == ('done',1)
            stage = 'expired_lease_requires_explicit_retry'
            second = save('-interrupted')
            old, inputs = jobs._claim(second['task']['id'], users[0])
            with jobs.transaction() as conn:
                conn.execute('UPDATE durable_jobs SET expires=0 WHERE id=?',(old['id'],))
            assert next(item for item in request('/api/tasks').json()['tasks'] if item['id'] == old['id'])['state'] == 'interrupted'
            assert not request('/api/tasks/'+old['id']+'/run','POST',data={}).json()['ok']
            assert request('/api/tasks/'+old['id']+'/retry','POST',data={}).status_code == 200
            assert request('/api/tasks/'+old['id']+'/run','POST',data={}).json()['ok']
            assert jobs._finish(old,users[0],inputs,{'summary_zh':'must not overwrite'})['state'] == 'interrupted'
            stage = 'memory_task'
            response = request('/api/profile','POST',data={'focus_areas':'Synthetic migration, not a real researcher'})
            memory = response.json()['task']['id']
            assert request('/api/tasks/'+memory+'/run','POST',data={}).json()['ok']
            with closing(connect_remote('library')) as conn:
                assert '合成验收记忆' in conn.execute('SELECT memory_core FROM user_profiles WHERE user_id=?',(users[0],)).fetchone()[0]
            stage = 'owner_config_persistence'
            previous = state.read_config('config')
            config = {'enabled':False,'preset':'custom','base_url':'https://synthetic.invalid/v1','api_key':'synthetic-not-a-real-key','model':'mock'}
            assert request('/api/settings/custom-llm','POST',who=0,data=config).status_code == 403
            assert request('/api/settings/custom-llm','POST',who=None,data=config,headers={'X-User-ID':OWNER}).status_code == 401
            response = request('/api/settings/custom-llm','POST',who=2,data=config)
            assert response.status_code == 200 and response.json()['custom']['has_key']
            assert 'synthetic-not-a-real-key' not in response.text
            assert request('/api/settings',who=2).json()['custom']['model'] == 'mock'
            assert state.read_config('config')['custom_provider']['model'] == 'mock'
            stage = 'shared_invitation_and_cooldown'
            trial_access.update(users[0],datetime.now(timezone.utc)+timedelta(hours=1))
            assert state.invitation_allowed(trial_access.key_for(users[0]))
            trial_access.update(users[0],revoke=True)
            assert not state.invitation_allowed(trial_access.key_for(users[0]))
            provider = {'name':'synthetic-'+run,'model':'mock','api_key':'not-real'}
            state.cooldown_provider(provider,60)
            assert state.provider_cooled(provider)
            print(json.dumps({'ok':True,'stage':'http_and_shared_state_complete','run_id':run,'real_model_calls':0}))
            stage = 'browser_reentry'
            if browser:
                third = save('-browser')
                env = {**os.environ,'PAPERMIND_TEST_URL':url,'PAPERMIND_TEST_SESSION':tokens[0],
                       'PAPERMIND_TEST_PAPER':str(third['id']),'PAPERMIND_TEST_JOB':third['task']['id'],
                       'PAPERMIND_TEST_UID':users[0]}
                assert subprocess.run(['node','--use-env-proxy',str(ROOT/'web/scripts/check-cloud-jobs.mjs')],env=env).returncode == 0
            print(json.dumps({'ok':True,'run_id':run,'checks':['durable_queue','repeated_save_deduplicated','other_account_denied',
                  'concurrent_execution_once','expired_lease_explicit_retry','stale_result_rejected','memory_saved',
                  'owner_config_remote_and_masked','invitation_revoked','shared_cooldown'],
                  'browser_checked':browser,'real_model_calls':0}))
        return 0
    except Exception as error:
        print(json.dumps({'ok':False,'stage':stage,'run_id':run,'error_type':type(error).__name__}))
        return 1
    finally:
        if previous is not None:
            state.write_config('config',previous)
        if tokens:
            with auth.db() as conn:
                for token in tokens:
                    conn.execute('DELETE FROM sessions WHERE hash=?',(auth.digest(token),))
        print(json.dumps({'run_id':run,'synthetic_sessions_revoked':len(tokens),'configuration_restored':previous is not None}))


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1], 'only' if '--browser-only' in sys.argv else '--browser' in sys.argv))
    except Exception:
        print(json.dumps({'ok':False,'stage':'target_or_cleanup'}))
        sys.exit(1)
