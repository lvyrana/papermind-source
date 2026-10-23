"""Optional remote libSQL connections; local SQLite remains the default.

Remote databases are prepared offline before cutover. Never create an empty
local fallback when a cloud URL, token, driver or schema is missing.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

SCHEMA_VERSION = 1
ROLES = {"library", "auth", "runtime"}


def remote_enabled():
    backend = os.environ.get("PAPERMIND_SQL_BACKEND", "sqlite").strip()
    if backend not in {"sqlite", "turso"}:
        raise RuntimeError("PAPERMIND_SQL_BACKEND must be sqlite or turso")
    return backend == "turso"


class Row:
    """The indexed/named/dict access used by our sqlite3.Row callers."""

    def __init__(self, description, values):
        self._names = tuple(column[0] for column in description)
        self._values = tuple(values)

    def keys(self):
        return list(self._names)

    def __getitem__(self, key):
        if isinstance(key, str):
            for index, name in enumerate(self._names):
                if name.lower() == key.lower():
                    return self._values[index]
            raise IndexError("No item with that key")
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)


class Cursor:
    def __init__(self, cursor):
        self._cursor = cursor

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    def fetchone(self):
        values = self._cursor.fetchone()
        return None if values is None else Row(self.description, values)

    def fetchall(self):
        return [Row(self.description, values) for values in self._cursor.fetchall()]

    def __iter__(self):
        while (row := self.fetchone()) is not None:
            yield row


class Connection:
    """Small explicit adapter: libsql 0.1.11 has no row_factory attribute."""

    def __init__(self, connection):
        self._connection = connection

    def execute(self, sql, parameters=()):
        return Cursor(self._connection.execute(sql, parameters))

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        self._connection.close()


def stamp_schema(connection, role):
    """Used only on an isolated, migrated snapshot, never an online request."""
    if role not in ROLES:
        raise ValueError("Unknown database role")
    connection.execute("CREATE TABLE IF NOT EXISTS _papermind_schema (role TEXT PRIMARY KEY, version INTEGER NOT NULL)")
    connection.execute("INSERT INTO _papermind_schema VALUES (?, ?) ON CONFLICT(role) DO UPDATE SET version=excluded.version",
                       (role, SCHEMA_VERSION))
    connection.commit()


def remote_credentials(role):
    """Accept either one complete legacy pair or Marketplace's native pair.

    Never combine a URL from one naming scheme with a token from the other.
    Conflicting pairs fail closed so stale manual configuration cannot silently
    select a different database from the project's managed integration.
    """
    if role not in ROLES:
        raise ValueError("Unknown database role")
    prefix = "PAPERMIND_" + role.upper()
    pairs = []
    for candidate in (prefix, prefix + "_TURSO"):
        url = os.environ.get(candidate + "_DATABASE_URL", "").strip()
        token = os.environ.get(candidate + "_AUTH_TOKEN", "").strip()
        if bool(url) != bool(token):
            raise RuntimeError(f"Configure a complete {candidate} credential pair; no local fallback")
        if url:
            pairs.append((url, token))
    if not pairs:
        raise RuntimeError(f"Configure {prefix} database credentials; no local fallback")
    if any(pair != pairs[0] for pair in pairs[1:]):
        raise RuntimeError(f"Conflicting {prefix} database credential pairs; no local fallback")
    return pairs[0]


def connect_remote(role):
    url, token = remote_credentials(role)
    prefix = "PAPERMIND_" + role.upper()
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme in {"libsql", "https"} and parsed.hostname and
                 not (parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment) and
                 parsed.path in {"", "/"} and token)
    except ValueError:
        valid = False
    if not valid:
        raise RuntimeError(f"Configure {prefix}_DATABASE_URL and {prefix}_AUTH_TOKEN; no local fallback")
    try:
        import libsql
    except ImportError:
        raise RuntimeError("Install the tested optional libsql dependency before enabling Turso") from None
    # No local replica path and no sync_url: writes go to the remote primary.
    try:
        raw = libsql.connect(url, auth_token=token)
    except Exception:
        raise RuntimeError(f"Cannot connect to the {role} database; no local fallback") from None
    conn = Connection(raw)
    try:
        row = conn.execute("SELECT version FROM _papermind_schema WHERE role=?", (role,)).fetchone()
        if row is None or row[0] != SCHEMA_VERSION:
            raise ValueError("Schema not prepared")
    except Exception:
        conn.close()
        raise RuntimeError(f"Prepare and verify the {role} migration snapshot before enabling Turso") from None
    return conn
