"""Shared cloud configuration and admission state; never fall back to /tmp.

app_config contains provider credentials. Exclude it from ordinary data exports;
restore those credentials separately through the authenticated owner settings.
"""
from contextlib import contextmanager
import hashlib
import json

from sql_backend import connect_remote
from runtime_limits import RuntimeStoreUnavailable

SCHEMA = """
CREATE TABLE app_config(name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE trial_invitations(uid_hash TEXT PRIMARY KEY, expires INTEGER NOT NULL, revoked INTEGER NOT NULL);
CREATE TABLE provider_cooldowns(key_hash TEXT PRIMARY KEY, expires INTEGER NOT NULL);
CREATE TABLE _papermind_state_schema(version INTEGER PRIMARY KEY);
INSERT INTO _papermind_state_schema VALUES(1);
"""


@contextmanager
def connection(write=False):
    conn = None
    try:
        conn = connect_remote('runtime')
        if conn.execute('SELECT version FROM _papermind_state_schema').fetchone()[0] != 1:
            raise ValueError('Unprepared state schema')
        if write:
            conn.execute('BEGIN IMMEDIATE')
        yield conn
        if write:
            conn.commit()
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        raise RuntimeStoreUnavailable('云端配置服务暂不可用，请稍后重试。') from None
    finally:
        if conn is not None:
            conn.close()


def _read(conn, name):
    row = conn.execute('SELECT value FROM app_config WHERE name=?', (name,)).fetchone()
    data = json.loads(row[0]) if row else {}
    if not isinstance(data, dict):
        raise ValueError('Invalid configuration')
    return data


def read_config(name):
    with connection() as conn:
        return _read(conn, name)


def write_config(name, data, *, patch=False):
    if name not in {'config', 'legacy-profile'} or not isinstance(data, dict):
        raise ValueError('Unknown configuration')
    with connection(write=True) as conn:
        if patch:
            data = {**_read(conn, name), **data}
        conn.execute('INSERT INTO app_config VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value',
                     (name, json.dumps(data, ensure_ascii=False)))


def invitation_allowed(uid_hash):
    with connection() as conn:
        return bool(conn.execute("SELECT 1 FROM trial_invitations WHERE uid_hash=? AND revoked=0 AND expires>CAST(strftime('%s','now') AS INTEGER)",
                                 (uid_hash,)).fetchone())


def update_invitation(uid_hash, expires=0, *, revoke=False):
    with connection(write=True) as conn:
        if revoke:
            conn.execute('INSERT INTO trial_invitations VALUES(?,0,1) ON CONFLICT(uid_hash) DO UPDATE SET revoked=1', (uid_hash,))
        else:
            now = conn.execute("SELECT CAST(strftime('%s','now') AS INTEGER)").fetchone()[0]
            if expires <= now:
                raise ValueError('Invitation has expired')
            conn.execute('INSERT INTO trial_invitations VALUES(?,?,0) ON CONFLICT(uid_hash) DO UPDATE SET expires=excluded.expires,revoked=0',
                         (uid_hash, expires))


def provider_key(provider):
    # Include endpoint and credential rotation, but never persist either.
    return hashlib.sha256(json.dumps([provider.get(key, '') for key in ('name','model','base_url','api_key')]).encode()).hexdigest()


def provider_cooled(provider):
    with connection() as conn:
        return bool(conn.execute("SELECT 1 FROM provider_cooldowns WHERE key_hash=? AND expires>CAST(strftime('%s','now') AS INTEGER)",
                                 (provider_key(provider),)).fetchone())


def cooldown_provider(provider, seconds):
    with connection(write=True) as conn:
        conn.execute("DELETE FROM provider_cooldowns WHERE expires<=CAST(strftime('%s','now') AS INTEGER)")
        conn.execute("INSERT INTO provider_cooldowns VALUES(?,CAST(strftime('%s','now') AS INTEGER)+?) ON CONFLICT(key_hash) DO UPDATE SET expires=MAX(expires,excluded.expires)",
                     (provider_key(provider), int(seconds)))
