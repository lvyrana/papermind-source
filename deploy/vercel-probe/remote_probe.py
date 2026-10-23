"""Bounded synthetic SQL checks. Never import or read product databases.

Initialization is an explicit CLI-only step on an empty database. HTTP checks
require that marker and never create schemas or accept caller-supplied SQL.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from urllib.parse import urlsplit
import uuid

import libsql

PURPOSE = "litfinch-migration-synthetic-v1"
MARKER = "_litfinch_probe_marker"
ROWS = "_litfinch_probe_rows"


class ProbeFailure(RuntimeError):
    """Messages are fixed stage names, never driver errors or credentials."""


def connect():
    url = os.environ.get("TURSO_DATABASE_URL", "").strip()
    token = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
    try:
        parts = urlsplit(url)
        valid = (parts.scheme in {"libsql", "https"} and
                 (parts.hostname or "").endswith(".turso.io") and
                 not (parts.username or parts.password or parts.port or
                      parts.query or parts.fragment) and parts.path in {"", "/"})
    except ValueError:
        valid = False
    if not valid or not token:
        raise ProbeFailure("invalid_synthetic_database_configuration")
    return libsql.connect(url, auth_token=token, timeout=5.0)


def verify_marker(conn):
    row = conn.execute(f"SELECT purpose FROM {MARKER} WHERE id=1").fetchone()
    if row != (PURPOSE,):
        raise ProbeFailure("synthetic_database_marker_mismatch")


def initialize_empty_database(factory=connect):
    conn = factory()
    try:
        conn.execute("BEGIN IMMEDIATE")
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()}
        if tables == {MARKER, ROWS}:
            verify_marker(conn)
        elif tables:
            raise ProbeFailure("refusing_nonempty_database")
        else:
            conn.execute(f"CREATE TABLE {MARKER} (id INTEGER PRIMARY KEY CHECK(id=1), purpose TEXT NOT NULL)")
            conn.execute(f"INSERT INTO {MARKER} VALUES (1, ?)", (PURPOSE,))
            conn.execute(f"CREATE TABLE {ROWS} (run_id TEXT NOT NULL, owner TEXT NOT NULL, value TEXT NOT NULL, counter INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(run_id, owner))")
        conn.commit()
        verify_marker(conn)
        return {"initialized": True, "purpose": PURPOSE}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_checks(factory=connect):
    started = time.perf_counter()
    run_id = uuid.uuid4().hex
    conn = None
    other = None
    stage = "connect"
    checks = {}
    # This value tests parameter binding, not SQL interpolation.
    value = "合成记录 '); DROP TABLE ignored; --"
    try:
        conn = factory()
        stage = "marker"
        verify_marker(conn)
        stage = "commit"
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(f"INSERT INTO {ROWS}(run_id, owner, value) VALUES (?, ?, ?)",
                     (run_id, "reader-a", value))
        conn.execute(f"INSERT INTO {ROWS}(run_id, owner, value) VALUES (?, ?, ?)",
                     (run_id, "reader-b", "synthetic-other-owner"))
        conn.commit()
        checks[stage] = True

        stage = "new_connection_read"
        other = factory()
        verify_marker(other)
        row = other.execute(f"SELECT value FROM {ROWS} WHERE run_id=? AND owner=?",
                            (run_id, "reader-a")).fetchone()
        if row != (value,):
            raise ProbeFailure(stage)
        checks[stage] = True

        stage = "owner_filter"
        rows = other.execute(f"SELECT owner, value FROM {ROWS} WHERE run_id=? AND owner=?",
                             (run_id, "reader-b")).fetchall()
        if rows != [("reader-b", "synthetic-other-owner")]:
            raise ProbeFailure(stage)
        checks[stage] = True
        other.close()
        other = None

        stage = "rollback"
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(f"UPDATE {ROWS} SET value=? WHERE run_id=? AND owner=?",
                     ("must-not-persist", run_id, "reader-a"))
        conn.rollback()
        other = factory()
        row = other.execute(f"SELECT value FROM {ROWS} WHERE run_id=? AND owner=?",
                            (run_id, "reader-a")).fetchone()
        if row != (value,):
            raise ProbeFailure(stage)
        checks[stage] = True
        other.close()
        other = None

        stage = "constraint_error_rollback"
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(f"UPDATE {ROWS} SET counter=counter+1 WHERE run_id=? AND owner=?",
                     (run_id, "reader-a"))
        rejected = False
        try:
            conn.execute(f"INSERT INTO {ROWS}(run_id, owner, value) VALUES (?, ?, ?)",
                         (run_id, "reader-a", "duplicate"))
        except Exception:
            rejected = True
        finally:
            conn.rollback()
        other = factory()
        row = other.execute(f"SELECT counter FROM {ROWS} WHERE run_id=? AND owner=?",
                            (run_id, "reader-a")).fetchone()
        if not rejected or row != (0,):
            raise ProbeFailure(stage)
        checks[stage] = True
        other.close()
        other = None

        stage = "atomic_increment"
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(f"UPDATE {ROWS} SET counter=counter+1 WHERE run_id=? AND owner=?",
                     (run_id, "reader-a"))
        conn.commit()
        conn.close()
        conn = None
        other = factory()
        row = other.execute(f"SELECT counter FROM {ROWS} WHERE run_id=? AND owner=?",
                            (run_id, "reader-a")).fetchone()
        if row != (1,):
            raise ProbeFailure(stage)
        checks[stage] = True
        # Keep two tiny synthetic rows per run as cross-process evidence. No
        # cleanup query can accidentally delete another run or a product table.
        return {"purpose": PURPOSE, "ok": True, "run_id": run_id,
                "checks": checks, "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "retained_synthetic_rows": 2, "product_deployed": False}
    except Exception:
        raise ProbeFailure(stage) from None
    finally:
        for item in (other, conn):
            if item is not None:
                try:
                    item.close()
                except Exception:
                    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initialize-empty", action="store_true")
    args = parser.parse_args()
    try:
        result = initialize_empty_database() if args.initialize_empty else run_checks()
    except Exception as error:
        # Driver messages may contain URLs. Only our own fixed stages are safe.
        stage = str(error) if isinstance(error, ProbeFailure) else "driver_error"
        print(json.dumps({"ok": False, "stage": stage}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
