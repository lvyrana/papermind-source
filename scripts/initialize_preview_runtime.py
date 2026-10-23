"""Add shared AI admission only to the approved, marked synthetic runtime DB."""
import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "papermind"))
from runtime_limits import REMOTE_SCHEMA
from sql_backend import remote_credentials


def migrate(conn):
    marker = conn.execute("SELECT purpose,role FROM _papermind_preview_init").fetchall()
    if [tuple(row) for row in marker] != [("litfinch-synthetic-business-v1", "runtime")]:
        raise ValueError("Not the synthetic runtime database")
    if conn.execute("SELECT name FROM sqlite_master WHERE name='_papermind_runtime_schema'").fetchone():
        if conn.execute("SELECT version FROM _papermind_runtime_schema").fetchall() != [(1,)]:
            raise ValueError("Unknown runtime schema")
        return "already_prepared"
    conn.executescript("BEGIN IMMEDIATE;\n" + REMOTE_SCHEMA + "\nCOMMIT;")
    return "initialized"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", choices=["litfinch-migration-preview"], required=True)
    parser.add_argument("--apply", action="store_true", required=True)
    parser.parse_args()
    import libsql
    url, token = remote_credentials("runtime")
    parsed = urlsplit(url)
    if not (parsed.scheme == "libsql" and parsed.hostname and
            parsed.hostname.startswith("litfinch-runtime-preview-") and parsed.hostname.endswith(".turso.io") and
            not (parsed.username or parsed.password or parsed.port or parsed.path or parsed.query or parsed.fragment)):
        raise ValueError("Wrong preview target")
    conn = libsql.connect(url, auth_token=token)
    try:
        print(json.dumps({"runtime_schema": migrate(conn), "synthetic_only": True}))
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(json.dumps({"ok": False, "stage": "runtime_schema_migration"}))
        sys.exit(1)
