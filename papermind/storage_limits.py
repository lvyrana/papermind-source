"""Atomic uploads and deletion transactions with cross-process quotas."""
from contextlib import contextmanager
from functools import partial
from pathlib import Path
import tempfile
from fastapi import HTTPException
from runtime_limits import file_lock, positive_setting


@contextmanager
def upload_transaction(data_dir):
    """Hold through fresh ownership queries, attachment changes and DB updates."""
    data_dir = Path(data_dir)
    with file_lock(data_dir / "runtime-limits" / "uploads.lock"):
        yield partial(_store_locked, data_dir=data_dir)


def store_upload(target, content, paper_ids, data_dir):
    with upload_transaction(data_dir) as store:
        store(target, content, paper_ids)


def _store_locked(target, content, paper_ids, data_dir):
    target = Path(target)
    ids = {str(number) for number in paper_ids}
    if (target.is_symlink() or target.parent.is_symlink()
            or target.parent.resolve() not in {data_dir.resolve() / "pdfs", data_dir.resolve() / "figures"}
            or target.stem.split("-")[0] not in ids):
        raise HTTPException(400, "Invalid upload destination")
    total = owned = 0
    for folder in ("pdfs", "figures"):
        for path in (data_dir / folder).glob("*"):
            if path.is_file() and not path.is_symlink():
                try:
                    size = path.stat().st_size
                except FileNotFoundError:
                    continue  # A simultaneous deletion can only free quota.
                total += size
                if path.stem.split("-")[0] in ids:
                    owned += size
    old_size = target.stat().st_size if target.exists() else 0
    delta = len(content) - old_size
    if total + delta > positive_setting("UPLOAD_TOTAL_LIMIT_MB", "2048") * 1024 * 1024:
        raise HTTPException(413, "服务器文件配额已满，请联系维护者。")
    if owned + delta > positive_setting("UPLOAD_USER_LIMIT_MB", "250") * 1024 * 1024:
        raise HTTPException(413, "当前设备文件配额已满，请先删除不需要的文件。")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
