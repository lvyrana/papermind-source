"""Prepare NEW private database copies from a verified backup. No network calls."""
from contextlib import closing
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "papermind"))
from backup_data import restore, verify, integrity, digest
from sql_backend import remote_enabled, stamp_schema, SCHEMA_VERSION
from src import database
import auth
from runtime_limits import REMOTE_SCHEMA
from cloud_jobs import SCHEMA as JOBS_SCHEMA
from cloud_state import SCHEMA as STATE_SCHEMA
from cloud_files import SCHEMA as FILE_SCHEMA
from cloud_figures import SCHEMA as FIGURE_SCHEMA
from cloud_maintenance import SCHEMA as MAINTENANCE_SCHEMA


def prepare(snapshot, destination):
    if remote_enabled():
        raise ValueError("Run preparation with PAPERMIND_SQL_BACKEND=sqlite; it never connects to Turso")
    manifest = verify(snapshot)
    if "auth.sqlite3" not in manifest["files"]:
        raise ValueError("Account database is missing; do not migrate without ownership mappings")
    destination = restore(snapshot, destination)
    old_library, old_auth = database.DB_PATH, auth.AUTH_DB
    try:
        database.DB_PATH = destination / "paperdiary.db"
        auth.AUTH_DB = destination / "auth.sqlite3"
        database.init_db()
        with auth.db():
            pass
    finally:
        database.DB_PATH, auth.AUTH_DB = old_library, old_auth
    paths = {"library": "paperdiary.db", "auth": "auth.sqlite3", "runtime": "runtime-limits/daily.sqlite3"}
    hashes = {}
    for role, name in paths.items():
        path = destination / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(path)) as conn:
            if role == 'library':
                conn.executescript(FILE_SCHEMA)
                conn.executescript(FIGURE_SCHEMA)
                conn.executescript(JOBS_SCHEMA)
            if role == "runtime":
                conn.executescript(REMOTE_SCHEMA)
                conn.executescript(STATE_SCHEMA)
                conn.executescript(MAINTENANCE_SCHEMA)
            stamp_schema(conn, role)
            # Standalone import file; do not leave a WAL sidecar dependency.
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("PRAGMA journal_mode=DELETE")
        integrity(path)
        hashes[role] = {"file": name, "sha256": digest(path)}
    receipt = {"format": "papermind-turso-preparation-v1", "schema_version": SCHEMA_VERSION,
               "source_snapshot": Path(snapshot).name, "databases": hashes,
               "uploaded": False, "sessions_revoked_in_copy": True,
               "remaining": ["fresh stop-write cutover snapshot", "separate production resources and credentials",
                             "attachment import and ledger mapping", "approved configuration and invitation policy",
                             "real OAuth and native Connector acceptance", "production backup and cutover"]}
    receipt_path = destination / "PREPARATION-RECEIPT.json"
    with receipt_path.open("x") as stream:
        json.dump(receipt, stream, indent=2)
        stream.write("\n")
    receipt_path.chmod(0o600)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.snapshot, args.destination)
    print("Prepared and verified 3 private database copies. No upload, DNS or live service changes.")


if __name__ == "__main__":
    main()
