"""Create and delete only this run's synthetic private PDF fixtures in Preview."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'papermind'))
import auth
import cloud_files
from sql_backend import connect_remote
from smoke_cloud_pdf import synthetic_pdf
from vercel.blob import put_blob, head_blob, BlobNotFoundError


async def absent(path):
    try:
        await head_blob(path)
    except BlobNotFoundError:
        return True
    return False


def main(url):
    if not re.fullmatch(r'https://litfinch-migration-preview-[a-z0-9]+-lvyranas-projects.vercel.app', url):
        raise ValueError('Wrong preview URL')
    if os.environ.get('BLOB_STORE_ID') != 'store_RkRS2huB7kCab3j8':
        raise ValueError('Wrong test Blob store')
    os.environ['PAPERMIND_SQL_BACKEND'] = 'turso'
    run = uuid.uuid4().hex
    tokens, papers, objects = [], [], []
    users = [str(uuid.uuid4()), str(uuid.uuid4())]
    content = synthetic_pdf(padding=32)
    stage = 'synthetic_markers'
    try:
        conn = connect_remote('library')
        try:
            assert tuple(conn.execute('SELECT purpose,role FROM _papermind_preview_init').fetchone()) == ('litfinch-synthetic-business-v1', 'library')
            assert conn.execute('SELECT version FROM _papermind_file_schema').fetchone()[0] == 2
        finally:
            conn.close()
        with auth.db() as conn:
            assert tuple(conn.execute('SELECT purpose,role FROM _papermind_preview_init').fetchone()) == ('litfinch-synthetic-business-v1', 'auth')
            for index, uid in enumerate(users):
                account = f'synthetic-cleanup-{run}-{index}'
                conn.execute('INSERT INTO accounts VALUES (?,?,?,?,?)', (account, account, 'Synthetic cleanup', uid, int(time.time())))
                token = auth.new_session(conn, account)
                conn.execute('UPDATE sessions SET expires=? WHERE hash=?', (int(time.time()) + 1800, auth.digest(token)))
                tokens.append(token)
        with httpx.Client(base_url=url, headers={'x-vercel-trusted-oidc-idp-token': os.environ['VERCEL_OIDC_TOKEN']}, timeout=60) as client:
            def request(method, path, who=0, body=None):
                return client.request(method, path, json=body, headers={'Authorization': 'Bearer ' + tokens[who]})

            def seed(label, who=0, state='ready', expired=True):
                response = request('POST', '/api/library/save', who, {'paper': {
                    'title': f'Synthetic cleanup {run[:8]} {label}', 'source': 'local_pdf', 'summary_zh': '合成清理测试。'}})
                assert response.status_code == 200
                paper = response.json()['id']
                papers.append(paper)
                row = cloud_files.reserve(paper, users[who], len(content))
                # No signed upload token is issued. These are deliberately old
                # fixture objects, not backdated still-valid client grants.
                asyncio.run(put_blob(row['pathname'], content, access='private', content_type='application/pdf',
                                     add_random_suffix=False, allow_overwrite=False, cache_control_max_age=60))
                objects.append(row['pathname'])
                with cloud_files.transaction() as conn:
                    conn.execute('UPDATE pdf_uploads SET state=?,expires=?,sha256=? WHERE id=? AND user_id=?',
                                 (state, int(time.time()) - cloud_files.GC_GRACE - 10 if expired else row['expires'],
                                  hashlib.sha256(content).hexdigest() if state == 'ready' else None, row['id'], users[who]))
                    conn.execute('UPDATE saved_papers SET has_pdf=? WHERE id=? AND user_id=?', (int(state == 'ready'), paper, users[who]))
                return paper, row

            stage = 'pdf_owner_and_immediate_revoke'
            first, first_row = seed('pdf')
            other, other_row = seed('other', who=1)
            assert request('DELETE', f'/api/library/{first}/pdf', who=1).status_code == 404
            assert request('GET', f'/api/library/{first}/pdf').status_code == 200
            result = request('DELETE', f'/api/library/{first}/pdf')
            assert result.status_code == 200 and result.json()['reclaimed'] == 1
            assert request('GET', f'/api/library/{first}/pdf').status_code == 404
            assert request('HEAD', f'/api/library/{first}/pdf').status_code == 404
            assert request('DELETE', f'/api/library/{first}/pdf').status_code == 200
            assert asyncio.run(absent(first_row['pathname']))
            assert request('GET', f'/api/library/{other}/pdf', who=1).status_code == 200
            print(json.dumps({'stage': stage, 'ok': True}), flush=True)

            stage = 'expired_and_recent_pending'
            expired, expired_row = seed('expired', state='pending')
            recent, recent_row = seed('recent', state='pending', expired=False)
            result = request('POST', '/api/file-storage/cleanup')
            assert result.status_code == 200 and result.json()['reclaimed'] == 1
            assert asyncio.run(absent(expired_row['pathname']))
            assert not asyncio.run(absent(recent_row['pathname']))
            # Revoke the recent test upload but preserve its object until the
            # normal grace period; do not weaken cleanup timing for the test.
            result = request('DELETE', f'/api/library/{recent}/pdf')
            assert result.status_code == 200 and result.json()['cleanup_pending'] == 1
            print(json.dumps({'stage': stage, 'ok': True}), flush=True)

            stage = 'paper_and_related_rows'
            assert request('POST', '/api/notes', body={'paper_rowid': other, 'content': 'Synthetic note'}, who=1).status_code == 200
            assert request('DELETE', f'/api/library/{other}').status_code == 404
            result = request('DELETE', f'/api/library/{other}', who=1)
            assert result.status_code == 200 and result.json()['reclaimed'] == 1
            assert request('GET', f'/api/library/{other}', who=1).json().get('error') == 'not found'
            assert asyncio.run(absent(other_row['pathname']))
            conn = connect_remote('library')
            try:
                assert conn.execute('SELECT COUNT(*) FROM paper_notes WHERE paper_rowid=?', (other,)).fetchone()[0] == 0
                reclaimed = conn.execute('SELECT COUNT(*) FROM pdf_cleanup g JOIN pdf_uploads p ON p.id=g.upload_id WHERE p.user_id IN (?,?)', users).fetchone()[0]
                charged = conn.execute('SELECT COALESCE(SUM(size),0) FROM pdf_uploads WHERE user_id IN (?,?) AND id NOT IN (SELECT upload_id FROM pdf_cleanup)', users).fetchone()[0]
                assert reclaimed == 3 and charged == len(content)
            finally:
                conn.close()
            print(json.dumps({'ok': True, 'run_id': run, 'papers': papers, 'objects_deleted': 3,
                              'recent_object_retained_for_grace': 1, 'bytes_per_object': len(content),
                              'checks': ['owner_delete', 'other_account_denied', 'expired_pending_cleanup',
                                         'recent_upload_protected', 'idempotent_pdf_delete', 'paper_related_rows_removed', 'quota_confirmed']}))
        return 0
    except Exception as error:
        print(json.dumps({'ok': False, 'stage': stage, 'run_id': run, 'error_type': type(error).__name__, 'papers': papers}))
        return 1
    finally:
        if tokens:
            with auth.db() as conn:
                for token in tokens:
                    conn.execute('DELETE FROM sessions WHERE hash=?', (auth.digest(token),))
            print(json.dumps({'run_id': run, 'synthetic_sessions_revoked': True}))


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
