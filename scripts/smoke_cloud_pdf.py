"""Seed short-lived synthetic accounts, run browser PDF flow, revoke sessions."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, urlunsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'papermind'))
import auth


def synthetic_pdf(padding=6 * 1024 * 1024):
    stream = b'BT /F1 20 Tf 50 740 Td (LitFinch synthetic private PDF) Tj ET\n%' + b'x' * padding + b'\n'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               f'<< /Length {len(stream)} >>\nstream\n'.encode() + stream + b'endstream']
    data, offsets = b'%PDF-1.4\n', []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n'
    xref = len(data)
    data += b'xref\n0 6\n0000000000 65535 f \n' + b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets)
    return data + f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()


def http_flow(url, paper, tokens):
    stage = 'grant'
    oidc = {'x-vercel-trusted-oidc-idp-token': os.environ['VERCEL_OIDC_TOKEN']}
    headers = {**oidc, 'Authorization': 'Bearer ' + tokens[0]}
    content = synthetic_pdf()
    digest = hashlib.sha256(content).hexdigest()
    try:
        with httpx.Client(base_url=url, headers=headers, timeout=90) as client:
            response = client.post('/api/file-grant', json={'paper_id': paper, 'size': len(content)})
            if response.status_code != 200:
                failure = response.json() if 'application/json' in response.headers.get('content-type', '') else {}
                print(json.dumps({'ok': False, 'stage': stage, 'status': response.status_code, 'upstream': failure.get('stage'), 'content_type': response.headers.get('content-type')}))
                return 1
            grant = response.json()
            signed = urlsplit(grant['upload_url'])
            assert (signed.scheme, signed.netloc, signed.path) == ('https', 'vercel.com', '/api/blob/')
            stage = 'direct_put'
            # Separate client: app identity/protection headers never reach Blob.
            with httpx.Client(timeout=120) as storage:
                response = storage.put(grant['upload_url'], content=content, headers={'Content-Type': 'application/pdf'})
                if not response.is_success:
                    print(json.dumps({'ok': False, 'stage': stage, 'status': response.status_code}))
                    return 1
                blob = response.json()
                stage = 'complete'
                response = client.post(f'/api/library/{paper}/pdf-upload/{grant["upload_id"]}')
                assert response.status_code == 200
                assert response.json()['sha256'] == digest
                stage = 'fresh_read'
                with httpx.Client(base_url=url, headers=headers, timeout=90) as fresh:
                    response = fresh.get(f'/api/library/{paper}/pdf')
                    assert response.status_code == 200 and hashlib.sha256(response.content).hexdigest() == digest
                    assert fresh.head(f'/api/library/{paper}/pdf').status_code == 200
                    assert fresh.get(f'/api/library/{paper}').json()['paper']['has_pdf']
                stage = 'isolation'
                other = {'Authorization': 'Bearer ' + tokens[1]}
                assert client.get(f'/api/library/{paper}/pdf', headers=other).status_code == 404
                assert client.post('/api/file-grant', json={'paper_id': paper, 'size': 10}, headers=other).status_code == 404
                private_url = urlsplit(blob['url'])
                assert private_url.scheme == 'https' and private_url.hostname.endswith('.private.blob.vercel-storage.com')
                unsigned = urlunsplit((private_url.scheme, private_url.netloc, private_url.path, '', ''))
                assert storage.get(unsigned).status_code == 403
                assert client.get(f'/api/library/{paper}/pdf', headers={'Authorization': ''}).status_code == 401
        print(json.dumps({'ok': True, 'paper_id': paper, 'bytes': len(content), 'sha256': digest,
                          'browser_verified': False, 'checks': ['signed_direct_put_over_4_5mb', 'completion_hash', 'fresh_stream_read', 'has_pdf_persisted', 'anonymous_blob_denied', 'other_account_read_write_denied']}))
        return 0
    except Exception as error:
        print(json.dumps({'ok': False, 'stage': stage, 'error_type': type(error).__name__}))
        return 1


def concurrent_reads(url, paper, token):
    """One real reader fan-out, bounded to the newly seeded synthetic account."""
    paths = ['/api/auth/status', f'/api/library/{paper}', f'/api/board/{paper}',
             f'/api/notes/{paper}', f'/api/cards/{paper}', '/api/library']
    with httpx.Client(base_url=url, timeout=60, headers={
            'x-vercel-trusted-oidc-idp-token': os.environ['VERCEL_OIDC_TOKEN'],
            'Authorization': 'Bearer ' + token}) as client, ThreadPoolExecutor(max_workers=6) as pool:
        started = time.monotonic()
        results = list(pool.map(client.get, paths))
    statuses = [response.status_code for response in results]
    print(json.dumps({'stage': 'concurrent_reader_requests', 'statuses': statuses,
                      'seconds': round(time.monotonic() - started, 2)}), flush=True)
    if statuses != [200] * len(paths):
        raise RuntimeError('Concurrent reader requests failed')


def main(url):
    import re
    if not re.fullmatch(r'https://litfinch-migration-preview-[a-z0-9]+-lvyranas-projects.vercel.app', url):
        raise ValueError('Invalid preview URL')
    os.environ['PAPERMIND_SQL_BACKEND'] = 'turso'
    run = uuid.uuid4().hex
    tokens, users = [], [str(uuid.uuid4()), str(uuid.uuid4())]
    stage = 'synthetic_accounts'
    try:
        with auth.db() as conn:
            if tuple(conn.execute('SELECT purpose,role FROM _papermind_preview_init').fetchone()) != ('litfinch-synthetic-business-v1', 'auth'):
                raise ValueError('Wrong database')
            for index, uid in enumerate(users):
                account = f'synthetic-pdf-{run}-{index}'
                conn.execute('INSERT INTO accounts VALUES (?,?,?,?,?)', (account, account, 'Synthetic PDF reader', uid, int(time.time())))
                token = auth.new_session(conn, account)
                conn.execute('UPDATE sessions SET expires=? WHERE hash=?', (int(time.time()) + 1800, auth.digest(token)))
                tokens.append(token)
        stage = 'save_synthetic_paper'
        with httpx.Client(base_url=url, timeout=60, headers={
                'x-vercel-trusted-oidc-idp-token': os.environ['VERCEL_OIDC_TOKEN'],
                'Authorization': 'Bearer ' + tokens[0]}) as client:
            response = client.post('/api/library/save', json={'paper': {'title': 'Synthetic PDF ' + run[:8], 'source': 'local_pdf', 'summary_zh': '合成测试文件，无真实文献。'}})
            response.raise_for_status()
            paper = response.json()['id']
        stage = 'browser_flow'
        if os.environ.get('PAPERMIND_PDF_HTTP_ONLY') == '1':
            return http_flow(url, paper, tokens)
        env = {**os.environ, 'PAPERMIND_TEST_URL': url, 'PAPERMIND_TEST_PAPER': str(paper),
               'PAPERMIND_TEST_SESSION': tokens[0], 'PAPERMIND_TEST_OTHER_SESSION': tokens[1],
               'PAPERMIND_TEST_UID': users[0]}
        if os.environ.get('PAPERMIND_PDF_CONNECTOR_ONLY') == '1':
            stage = 'concurrent_reader_requests'
            concurrent_reads(url, paper, tokens[0])
            stage = 'connector_upload'
            result = subprocess.run(['node', '--use-env-proxy', str(ROOT / 'papermind-zotero/scripts/check-cloud-upload.mjs')],
                                    env=env, input=synthetic_pdf())
            return result.returncode
        result = subprocess.run(['node', '--use-env-proxy', str(ROOT / 'web/scripts/check-cloud-pdf.mjs')], env=env)
        return result.returncode
    except Exception:
        print(json.dumps({'ok': False, 'stage': stage, 'run_id': run}))
        return 1
    finally:
        if tokens:
            with auth.db() as conn:
                for token in tokens:
                    conn.execute('DELETE FROM sessions WHERE hash=?', (auth.digest(token),))
            print(json.dumps({'run_id': run, 'synthetic_sessions_revoked': True}))


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
