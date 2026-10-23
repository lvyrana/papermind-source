"""Initialize empty preview databases from current business schemas, never data.

Run through the linked preview project's environment. No credentials are printed.
Existing databases are never overwritten; matching initialization is idempotent.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "papermind"))
import auth
from src import database
from sql_backend import SCHEMA_VERSION, remote_credentials

PROJECT = "litfinch-migration-preview"
PURPOSE = "litfinch-synthetic-business-v1"

class PreviewInitializationError(RuntimeError):
    """Only fixed, non-sensitive stage messages may use this exception."""


def schema_scripts():
    """Use fresh local files so no production records can enter the payload."""
    old_library, old_auth = database.DB_PATH, auth.AUTH_DB
    old_backend = os.environ.get("PAPERMIND_SQL_BACKEND")
    try:
        os.environ["PAPERMIND_SQL_BACKEND"] = "sqlite"
        with tempfile.TemporaryDirectory(prefix="litfinch-empty-schema-") as temporary:
            root = Path(temporary)
            database.DB_PATH, auth.AUTH_DB = root / "library.db", root / "auth.db"
            database.init_db()
            with auth.db():
                pass
            with closing(sqlite3.connect(root / "runtime.db")) as conn:
                conn.execute("CREATE TABLE attempts (day TEXT PRIMARY KEY, count INTEGER NOT NULL)")
                conn.commit()
            result = {}
            for role in ("library", "auth", "runtime"):
                with closing(sqlite3.connect(root / (role + ".db"))) as conn:
                    rows = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
                    for table, _ in rows:
                        if conn.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0]:
                            raise PreviewInitializationError("Schema source is not empty")
                    ddl = [row[0] for row in conn.execute(
                        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
                        "ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, name")]
                script = ";\n".join(ddl) + ";\n"
                fingerprint = hashlib.sha256(script.encode()).hexdigest()
                result[role] = (script, fingerprint, len(rows))
            return result
    finally:
        database.DB_PATH, auth.AUTH_DB = old_library, old_auth
        if old_backend is None:
            os.environ.pop("PAPERMIND_SQL_BACKEND", None)
        else:
            os.environ["PAPERMIND_SQL_BACKEND"] = old_backend


def state(conn, role, fingerprint):
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    if not tables:
        return "empty"
    try:
        row = conn.execute("SELECT purpose, role, fingerprint FROM _papermind_preview_init").fetchall()
        schema = conn.execute("SELECT role, version FROM _papermind_schema").fetchall()
    except Exception:
        raise PreviewInitializationError(role + ": refusing existing unmarked database") from None
    if [tuple(item) for item in row] != [(PURPOSE, role, fingerprint)] or [tuple(item) for item in schema] != [(role, SCHEMA_VERSION)]:
        raise PreviewInitializationError(role + ": refusing mismatched preview schema")
    return "prepared"


def initialize(conn, role, script, fingerprint):
    if state(conn, role, fingerprint) == "prepared":
        return "already_prepared"
    # One server-side script avoids a round trip per DDL statement and keeps
    # the initial schema and its completion marker in one transaction.
    transaction = ("BEGIN IMMEDIATE;\n" + script +
        "CREATE TABLE _papermind_schema (role TEXT PRIMARY KEY, version INTEGER NOT NULL);\n" +
        f"INSERT INTO _papermind_schema VALUES ('{role}', {SCHEMA_VERSION});\n" +
        "CREATE TABLE _papermind_preview_init (purpose TEXT NOT NULL, role TEXT NOT NULL, fingerprint TEXT NOT NULL);\n" +
        f"INSERT INTO _papermind_preview_init VALUES ('{PURPOSE}', '{role}', '{fingerprint}');\nCOMMIT;")
    try:
        conn.executescript(transaction)
    except Exception:
        conn.rollback()
        raise PreviewInitializationError(role + ": initialization failed; inspect before retry") from None
    if state(conn, role, fingerprint) != "prepared":
        raise PreviewInitializationError(role + ": initialization not confirmed")
    return "initialized"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", choices=[PROJECT], required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    scripts = schema_scripts()
    if not args.apply:
        print(json.dumps({"project": args.project, "tables": {r: item[2] for r, item in scripts.items()}, "applied": False}))
        return
    import libsql
    connections = {}
    try:
        # Check every target before initializing the first one.
        hosts = set()
        for role, (_, fingerprint, _) in scripts.items():
            url, token = remote_credentials(role)
            parsed = urlsplit(url)
            if (parsed.scheme != "libsql" or not parsed.hostname or
                    not parsed.hostname.startswith("litfinch-" + role + "-preview-") or
                    not parsed.hostname.endswith(".turso.io") or
                    parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment or parsed.path):
                raise PreviewInitializationError(role + ": not the approved preview database")
            if parsed.hostname in hosts:
                raise PreviewInitializationError("Preview role databases must be distinct")
            hosts.add(parsed.hostname)
            connections[role] = libsql.connect(url, auth_token=token, timeout=10)
            state(connections[role], role, fingerprint)
        result = {}
        for role, (script, fingerprint, count) in scripts.items():
            result[role] = {"state": initialize(connections[role], role, script, fingerprint), "business_tables": count}
        print(json.dumps({"project": args.project, "synthetic_only": True, "databases": result}))
    finally:
        for conn in connections.values():
            conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Driver errors may contain credentials: withhold details and traceback.
        detail = str(error) if isinstance(error, PreviewInitializationError) else "preview initialization failed"
        print(json.dumps({"ok": False, "stage": detail}))
        raise SystemExit(1)
