"""Production entrypoint template. Does not provision resources or migrate data."""
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
from uuid import UUID

if os.environ.get('VERCEL_ENV') != 'production' or os.environ.get('PAPERMIND_MIGRATION_APPROVED') != 'true':
    raise RuntimeError('Production cutover must be explicitly configured')
if os.environ.get('PAPERMIND_SYNTHETIC_TASKS') or os.environ.get('PAPERMIND_SQL_BACKEND') != 'turso' or os.environ.get('PAPERMIND_FILE_BACKEND') != 'vercel-blob':
    raise RuntimeError('Production backends are not prepared')
if not os.environ.get('BLOB_STORE_ID') or os.environ['BLOB_STORE_ID'] == 'store_RkRS2huB7kCab3j8':
    raise RuntimeError('Use separate production storage')
for key in ('OWNER_UID','ZOTERO_OAUTH_CLIENT_KEY','ZOTERO_OAUTH_CLIENT_SECRET','PAPERMIND_PUBLIC_ORIGIN','CRON_SECRET'):
    if not os.environ.get(key):
        raise RuntimeError('Production configuration is incomplete')
origin = os.environ['PAPERMIND_PUBLIC_ORIGIN']
parsed = urlsplit(origin)
if parsed.scheme != 'https' or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
    raise RuntimeError('Use a canonical HTTPS production origin')
if len(os.environ['CRON_SECRET']) < 32 or UUID(os.environ['OWNER_UID']).version != 4:
    raise RuntimeError('Owner or maintenance authentication is incomplete')
os.environ['ALLOWED_ORIGINS'] = origin
sys.path.insert(0,str(Path(__file__).resolve().parent/'papermind'))
from sql_backend import remote_credentials
hosts = set()
for role in ('library','auth','runtime'):
    host = urlsplit(remote_credentials(role)[0]).hostname or ''
    if not host or '-preview-' in host:
        raise RuntimeError('Use separate production databases')
    hosts.add(host)
if len(hosts) != 3:
    raise RuntimeError('Production database roles must use distinct databases')

from api import app
from fastapi.responses import JSONResponse


@app.middleware('http')
async def production_scope(request,call_next):
    # The old recommendation feed is already hidden by the product flag. Its
    # daemon/cache implementation must not reappear in a serverless deployment.
    if request.url.path == '/api/papers' or (request.url.path.startswith('/api/papers/') and request.url.path.removeprefix('/api/papers/').isdigit()):
        return JSONResponse({'ok':False,'detail':'旧推荐列表未在云端启用，请从书架添加文献。'},status_code=503)
    response = await call_next(request)
    if request.url.path.startswith(('/api/','/internal/')):
        response.headers['Cache-Control'] = 'private, no-store'
    return response
