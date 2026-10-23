"""Private daily cloud backup with a fresh-directory restore verification."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import sys
import uuid
from dotenv import dotenv_values
from backup_cloud import backup
from backup_data import restore,verify
from import_cloud_production import BLOB,validate_targets


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args()
    os.umask(0o077)
    if args.credentials.is_symlink() or args.credentials.stat().st_mode & 0o077:
        raise ValueError('Credentials must be a private regular file')
    values=dotenv_values(args.credentials)
    allowed={'BLOB_READ_WRITE_TOKEN'} | {f'PAPERMIND_{role}_TURSO_{suffix}' for role in ('LIBRARY','AUTH','RUNTIME') for suffix in ('DATABASE_URL','AUTH_TOKEN')}
    if not all(values.get(key) for key in allowed):
        raise ValueError('Missing backup credentials')
    os.environ.pop('VERCEL_OIDC_TOKEN',None)
    os.environ.update({key:values[key] for key in allowed})
    os.environ['BLOB_STORE_ID']=BLOB
    os.environ['PAPERMIND_SQL_BACKEND']='turso'
    validate_targets()
    snapshot=backup(args.destination/'snapshots')
    check=args.destination/'restore-checks'/(snapshot.name+'-'+uuid.uuid4().hex[:6])
    restore(snapshot,check)
    manifest=verify(snapshot)
    receipt={'ok':True,'snapshot':str(snapshot),'restore_check':str(check),
             'files':len(manifest['files']),'attachments':manifest['cloud_attachments'],
             'completed_at':datetime.now(timezone.utc).isoformat()}
    args.destination.mkdir(parents=True,exist_ok=True,mode=0o700)
    # No automatically deleted snapshots; the existing 10 GiB storage guard
    # fails closed and preserves older recovery points when the budget is full.
    marker=args.destination/'LAST-SUCCESS.json'
    temporary=marker.with_suffix('.tmp')
    temporary.write_text(json.dumps(receipt,indent=2)+'\n')
    temporary.replace(marker)
    print(json.dumps(receipt),flush=True)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print(json.dumps({'ok':False,'stage':'daily_cloud_backup','error_type':type(error).__name__}),flush=True)
        sys.exit(1)
