"""Verified private backups; restore only into a NEW directory, never over live data."""
from __future__ import annotations

import argparse
from contextlib import closing, ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
OPTIONAL_DATABASES = ("runtime-limits/daily.sqlite3", "auth.sqlite3")


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def safe_name(name):
    parts = PurePosixPath(name).parts
    return (name in ("paperdiary.db", *OPTIONAL_DATABASES) or (len(parts) > 1 and parts[0] in ("pdfs", "figures"))) \
        and all(part not in (".", "..") for part in parts) and "\\" not in name \
        and not name.startswith("/")


def file_inventory(data, include_files=True):
    result = {}
    for folder in ("pdfs", "figures") if include_files else ():
        parent = data / folder
        if parent.is_symlink():
            raise ValueError("Refusing linked data directory")
        if not parent.exists():
            continue
        for path in sorted(parent.rglob("*")):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ValueError("Refusing linked or special data file")
            if path.is_file():
                stat = path.stat()
                result[path.relative_to(data).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return result


def integrity(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("SQLite integrity check failed")


def verify(backup):
    backup = Path(backup)
    if backup.is_symlink() or (backup / "manifest.json").is_symlink():
        raise ValueError("Refusing linked backup")
    manifest = json.loads((backup / "manifest.json").read_text())
    if manifest.get("format") != "papermind-backup-v1" or "paperdiary.db" not in manifest.get("files", {}):
        raise ValueError("Invalid backup manifest")
    expected = set(manifest["files"]) | {"manifest.json"}
    actual = set()
    for path in backup.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("Refusing linked or special backup member")
        if path.is_file():
            actual.add(path.relative_to(backup).as_posix())
    if actual != expected:
        raise ValueError("Backup file set does not match manifest")
    for name, metadata in manifest["files"].items():
        if not safe_name(name):
            raise ValueError("Invalid backup member path")
        path = backup / name
        if path.stat().st_size != metadata["bytes"] or digest(path) != metadata["sha256"]:
            raise ValueError("Backup hash or size mismatch")
    integrity(backup / "paperdiary.db")
    for name in OPTIONAL_DATABASES:
        if name in manifest["files"]:
            integrity(backup / name)
    return manifest


def create(data, destination, include_files=True):
    data, destination = Path(data), Path(destination)
    if data.is_symlink() or (data / "paperdiary.db").is_symlink() or destination.is_symlink():
        raise ValueError("Refusing linked backup source or destination")
    data, destination = data.resolve(), destination.resolve()
    if destination == data or data in destination.parents or destination in data.parents \
            or destination == Path.home().resolve():
        raise ValueError("Backups must be outside data/")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination, 0o700)
    stage = Path(tempfile.mkdtemp(prefix=".incomplete-", dir=destination))
    try:
        before = file_inventory(data, include_files)
        # Retain old backups, but fail before copying when disk space is low.
        # This avoids silently deleting the only useful recovery point.
        estimated = (data / "paperdiary.db").stat().st_size + sum(size for size, _ in before.values())
        estimated += sum(path.stat().st_size for path in data.glob("paperdiary.db-wal"))
        estimated += sum((data / name).stat().st_size for name in OPTIONAL_DATABASES if (data / name).exists())
        budget_mb = int(os.environ.get("PAPERMIND_BACKUP_MAX_MB", "10240"))
        if budget_mb < 1:
            raise ValueError("PAPERMIND_BACKUP_MAX_MB must be positive")
        stored = sum(p.stat().st_size for p in destination.rglob("*") if p.is_file() and not p.is_symlink())
        if stored + estimated > budget_mb * 1024 * 1024:
            raise RuntimeError("Backup storage budget reached; archive verified snapshots to controlled storage before retrying")
        if shutil.disk_usage(destination).free < estimated * 1.1 + 512 * 1024 * 1024:
            raise RuntimeError("Insufficient free space for a new backup plus 512 MiB reserve")
        with ExitStack() as stack:
            sources = []
            names = ["paperdiary.db"]
            optional_present = {name: (data / name).exists() for name in OPTIONAL_DATABASES}
            for name, present in optional_present.items():
                if (data / name).is_symlink() or (data / name).parent.is_symlink():
                    raise ValueError("Refusing linked authentication or budget state")
                if present:
                    names.append(name)
            for name in names:
                source = stack.enter_context(closing(sqlite3.connect((data / name).as_uri() + "?mode=ro", uri=True)))
                sources.append((source, source.execute("PRAGMA data_version").fetchone()[0]))
                target_path = stage / name
                target_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with closing(sqlite3.connect(target_path)) as target:
                    source.backup(target)
                # Reopen after backup to read the copied file's journal mode.
                with closing(sqlite3.connect(target_path)) as target:
                    mode = target.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
                    if mode.lower() != "delete":
                        raise RuntimeError("Backup database must not retain a live WAL journal")
                # Some SQLite builds leave an orphan SHM after WAL -> DELETE.
                # Only clean sidecars of this closed, newly created stage DB;
                # a non-empty WAL means the snapshot cannot be published.
                wal = Path(str(target_path) + "-wal")
                shm = Path(str(target_path) + "-shm")
                if wal.exists() and wal.stat().st_size:
                    raise RuntimeError("Backup database has uncheckpointed WAL data")
                wal.unlink(missing_ok=True)
                shm.unlink(missing_ok=True)
                os.chmod(target_path, 0o600)
            for name in before:
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copyfile(data / name, target)
                os.chmod(target, 0o600)
            if any(revision != source.execute("PRAGMA data_version").fetchone()[0] for source, revision in sources) \
                    or any(present != (data / name).exists() for name, present in optional_present.items()) \
                    or before != file_inventory(data, include_files):
                raise RuntimeError("Data changed during backup; retry in a quiet window or stop the service")
        files = {}
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                files[path.relative_to(stage).as_posix()] = {"bytes": path.stat().st_size, "sha256": digest(path)}
        manifest = {"format": "papermind-backup-v1", "created_at": datetime.now(timezone.utc).isoformat(),
                    "includes_files": include_files, "files": files,
                    "excluded": ["config.json", ".env", "trial-access.json", "runtime-limits/*.lock"],
                    "restore_requires": "Preserve account ownership. Sessions and pending logins are revoked on restore. Re-authorize trial UUIDs against CURRENT consent.",
                    "consistency": "no observed DB/file changes during snapshot"}
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        os.chmod(stage / "manifest.json", 0o600)
        verify(stage)
        name = "snapshot-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
        final = destination / name
        stage.rename(final)
        return final
    finally:
        if stage.exists():
            shutil.rmtree(stage)  # Only our newly created, private temporary stage.


def restore(backup, destination):
    """A new data directory prevents mixing WAL/SHM and files from different dates."""
    manifest = verify(backup)
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError("Restore destination must not exist; never overwrite a running data directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".restore-", dir=destination.parent))
    try:
        for name in manifest["files"]:
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(Path(backup) / name, target)
            os.chmod(target, 0o600)
            if digest(target) != manifest["files"][name]["sha256"]:
                raise ValueError("Restore verification failed")
        integrity(stage / "paperdiary.db")
        for name in OPTIONAL_DATABASES:
            if name in manifest["files"]:
                integrity(stage / name)
        if "auth.sqlite3" in manifest["files"]:
            # Keep ownership, but never resurrect a previously revoked session.
            with closing(sqlite3.connect(stage / "auth.sqlite3")) as auth_db:
                auth_db.execute("PRAGMA secure_delete=ON")
                for table in ("sessions", "flows", "devices", "attempts"):
                    auth_db.execute(f"DELETE FROM {table}")
                auth_db.commit()
            integrity(stage / "auth.sqlite3")
        destination.mkdir(mode=0o700)
        try:
            for item in stage.iterdir():
                item.rename(destination / item.name)
        except BaseException:
            shutil.rmtree(destination)  # Newly created by this restore only.
            raise
        return destination
    finally:
        shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("create")
    make.add_argument("--data-dir", type=Path, default=ROOT / "papermind/data")
    make.add_argument("--backup-dir", type=Path, default=ROOT / "backups")
    make.add_argument("--database-only", action="store_true")
    check = sub.add_parser("verify")
    check.add_argument("snapshot", type=Path)
    recover = sub.add_parser("restore")
    recover.add_argument("snapshot", type=Path)
    recover.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.command == "create":
            print(create(args.data_dir, args.backup_dir, not args.database_only))
        elif args.command == "verify":
            manifest = verify(args.snapshot)
            print(f"Verified {len(manifest['files'])} files and SQLite integrity; no contents printed.")
        else:
            print(restore(args.snapshot, args.destination))
            print("Invitation grants excluded: re-authorize against current consent before reopening trial access.")
    except (ValueError, RuntimeError, OSError, sqlite3.Error, KeyError) as error:
        parser.exit(1, f"Backup operation failed: {error}\n")


if __name__ == "__main__":
    main()
