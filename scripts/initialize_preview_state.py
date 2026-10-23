"""Initialize shared settings only on the approved synthetic runtime database."""
import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'papermind'))
from cloud_state import SCHEMA
from sql_backend import remote_credentials


def migrate(conn):
    if [tuple(row) for row in conn.execute('SELECT purpose,role FROM _papermind_preview_init').fetchall()] != [('litfinch-synthetic-business-v1', 'runtime')]:
        raise ValueError('Wrong synthetic database')
    if conn.execute("SELECT name FROM sqlite_master WHERE name='_papermind_state_schema'").fetchone():
        if conn.execute('SELECT version FROM _papermind_state_schema').fetchall() != [(1,)]:
            raise ValueError('Unknown state schema')
        return 'already_prepared'
    conn.executescript('BEGIN IMMEDIATE;\n' + SCHEMA + '\nCOMMIT;')
    return 'initialized'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', choices=['litfinch-migration-preview'], required=True)
    parser.add_argument('--apply', action='store_true', required=True)
    parser.parse_args()
    try:
        import libsql
        url, token = remote_credentials('runtime')
        parsed = urlsplit(url)
        if not (parsed.scheme == 'libsql' and parsed.hostname and parsed.hostname.startswith('litfinch-runtime-preview-')
                and parsed.hostname.endswith('.turso.io') and not (parsed.username or parsed.password or parsed.port or parsed.path or parsed.query or parsed.fragment)):
            raise ValueError('Wrong preview target')
        conn = libsql.connect(url, auth_token=token)
        try:
            print(json.dumps({'state_schema': migrate(conn), 'synthetic_only': True}))
        finally:
            conn.close()
    except Exception:
        print(json.dumps({'ok': False, 'stage': 'state_schema_migration'}))
        sys.exit(1)
