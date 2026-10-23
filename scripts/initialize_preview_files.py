"""Add only the private-file ledger to the marked synthetic preview database."""
import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "papermind"))
from cloud_files import SCHEMA, GC_SCHEMA
from sql_backend import remote_credentials


def migrate(conn):
    marker = conn.execute("SELECT purpose,role FROM _papermind_preview_init").fetchall()
    if [tuple(row) for row in marker] != [("litfinch-synthetic-business-v1", "library")]:
        raise RuntimeError("Not the synthetic library database")
    if conn.execute("SELECT name FROM sqlite_master WHERE name='_papermind_file_schema'").fetchone():
        versions = conn.execute("SELECT version FROM _papermind_file_schema").fetchall()
        if versions == [(1,)]:
            conn.executescript("BEGIN IMMEDIATE;\n" + GC_SCHEMA + "\nUPDATE _papermind_file_schema SET version=2;\nCOMMIT;")
            return "upgraded_to_2"
        if versions != [(2,)]:
            raise RuntimeError("Unknown file schema")
        return "already_prepared"
    conn.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")
    return "initialized"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", choices=["litfinch-migration-preview"], required=True)
    parser.add_argument("--apply", action="store_true", required=True)
    parser.parse_args()
    try:
        import libsql
        url, token = remote_credentials("library")
        parsed = urlsplit(url)
        if not (parsed.scheme == "libsql" and parsed.hostname.startswith("litfinch-library-preview-") and parsed.hostname.endswith(".turso.io")):
            raise RuntimeError("Not the preview target")
        conn = libsql.connect(url, auth_token=token)
        try:
            print(json.dumps({"file_schema": migrate(conn), "synthetic_only": True}))
        finally:
            conn.close()
    except Exception:
        print(json.dumps({"ok": False, "stage": "file_schema_migration"}))
        sys.exit(1)
