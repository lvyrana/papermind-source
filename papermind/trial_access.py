"""Optional admission list over existing device UUIDs; no data-identity migration."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from uuid import UUID
from runtime_limits import file_lock

ACCESS_FILE = Path(__file__).parent / "data" / "trial-access.json"


def canonical_uid(value):
    uid = str(UUID(value.strip().lower()))
    if UUID(uid).version != 4:
        raise ValueError("A UUID v4 is required")
    return uid


def key_for(uid):
    return hashlib.sha256(canonical_uid(uid).encode()).hexdigest()


def allowed(uid, owner="", now=None):
    if os.environ.get("PAPERMIND_INVITE_ONLY", "").lower() != "true":
        return True
    if owner and uid == owner:
        return True
    try:
        if ACCESS_FILE.is_symlink():
            return False
        entries = json.loads(ACCESS_FILE.read_text())
        if not isinstance(entries, dict):
            return False
        entry = entries.get(key_for(uid), {})
        if not isinstance(entry, dict):
            return False
        expires = datetime.fromisoformat(entry["expires_at"])
        return not entry.get("revoked", False) and expires > (now or datetime.now(timezone.utc))
    except (OSError, ValueError, TypeError, KeyError):
        return False


def update(uid, expires_at=None, revoke=False):
    uid = canonical_uid(uid)
    ACCESS_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with file_lock(ACCESS_FILE.with_suffix(".lock")):
        if ACCESS_FILE.is_symlink():
            raise ValueError("Refusing linked invitation store")
        entries = json.loads(ACCESS_FILE.read_text()) if ACCESS_FILE.exists() else {}
        if not isinstance(entries, dict):
            raise ValueError("Invalid invitation store; refusing to overwrite it")
        key = key_for(uid)
        if revoke:
            entries[key] = {**entries.get(key, {}), "revoked": True}
        else:
            if expires_at.tzinfo is None or expires_at <= datetime.now(timezone.utc):
                raise ValueError("Invitation expiry must be timezone-aware and in the future")
            entries[key] = {"expires_at": expires_at.isoformat(), "revoked": False}
        with tempfile.NamedTemporaryFile(mode="w", dir=ACCESS_FILE.parent, delete=False) as stream:
            path = Path(stream.name)
            json.dump(entries, stream, indent=2)
        try:
            path.replace(ACCESS_FILE)
        finally:
            path.unlink(missing_ok=True)
