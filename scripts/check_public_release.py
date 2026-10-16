"""Read-only, limited preflight for current Git candidates; never prints secrets.

This is not a history scan, a license opinion, or a comprehensive secret scanner.
Exit 0 means only these automated checks passed, not permission to publish.
"""
from pathlib import Path, PurePosixPath
import argparse
import re
import subprocess


SECRET_PATTERNS = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("provider-key", re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("aws-key-id", re.compile(r"\bAKIA[A-Z0-9]{16}\b")),
)
PRIVATE_PARTS = {"data", "backups", "软著申请", "conference-2026", ".git",
                 "node_modules", ".venv", ".venv_new", ".venv_mcp", "papermind-zotero"}
# Exact static source exception only; secret and symlink checks still apply.
PUBLIC_SOURCE_PATH_EXCEPTIONS = frozenset({"web/src/data/demoPaper.js"})
BINARY_REVIEW_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".pdf", ".docx", ".xlsx", ".xpi", ".zip"}


def private_path(path):
    p = PurePosixPath(path)
    name = p.name.lower()
    return (
        (path not in PUBLIC_SOURCE_PATH_EXCEPTIONS and any(part in PRIVATE_PARTS for part in p.parts))
        or (name.startswith(".env") and name != ".env.example")
        or name.endswith((".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".pem", ".key"))
    )


def inspect_file(path, content):
    """Return rule names/line numbers only. Matched values never leave this function."""
    issues = []
    if private_path(path):
        issues.append(("private-path", 0))
    try:
        source = content.decode("utf-8")
    except UnicodeDecodeError:
        return issues
    for rule, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(source):
            issues.append((rule, source.count("\n", 0, match.start()) + 1))
    if PurePosixPath(path).name == ".env.example":
        for line_number, line in enumerate(source.splitlines(), 1):
            match = re.match(r"\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*([^#]*)", line)
            if not match:
                continue
            key, value = match.groups()
            if (key.endswith(("_API_KEY", "_TOKEN", "_PASSWORD")) or key == "OWNER_UID"):
                if value.strip().strip("\"'"):
                    issues.append(("nonempty-example-credential", line_number))
    return issues


def check(root, require_license=True):
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root, check=True, stdout=subprocess.PIPE,
    )
    paths = sorted(set(p.decode("utf-8") for p in result.stdout.split(b"\0") if p))
    findings, review = [], []
    for name in paths:
        path = root / name
        if path.is_symlink():
            findings.append((name, "symlink-needs-review", 0))
            continue
        if not path.is_file():
            findings.append((name, "missing-or-nested-repository", 0))
            continue
        for rule, line in inspect_file(name, path.read_bytes()):
            findings.append((name, rule, line))
        if path.suffix.lower() in BINARY_REVIEW_SUFFIXES:
            review.append(name)
    if require_license:
        licenses = [root / name for name in ("LICENSE", "LICENSE.md", "LICENSE.txt") if name in paths]
        if not any(p.is_file() and not p.is_symlink() and p.read_text().strip() for p in licenses):
            findings.append(("LICENSE", "main-project-license-not-confirmed", 0))
    return paths, findings, review


def check_history(root):
    """Inspect reachable local Git blobs; return locations/rules, never matched values."""
    objects = subprocess.check_output(["git", "rev-list", "--objects", "--all"], cwd=root).splitlines()
    entries = [line.split(b" ", 1) for line in objects if b" " in line]
    metadata = subprocess.check_output(
        ["git", "cat-file", "--batch-check=%(objecttype) %(objectsize)"],
        cwd=root, input=b"\n".join(oid for oid, _ in entries) + b"\n",
    ).splitlines()
    findings, skipped, scanned = [], 0, 0
    for (oid, raw_name), meta in zip(entries, metadata):
        kind, size = meta.split()
        if kind != b"blob":
            continue
        name = raw_name.decode("utf-8", errors="replace")
        if int(size) > 2_000_000:
            skipped += 1
            if private_path(name):
                findings.append((oid.decode()[:12], name, "private-path", 0))
            continue
        content = subprocess.check_output(["git", "cat-file", "blob", oid.decode()], cwd=root)
        scanned += 1
        for rule, line in inspect_file(name, content):
            findings.append((oid.decode()[:12], name, rule, line))
    return scanned, skipped, findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code-only", action="store_true",
                        help="skip license-presence gate for development checks; not a release approval")
    parser.add_argument("--history", action="store_true", help="also scan blobs reachable from all local refs (no remote fetch)")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    paths, findings, review = check(root, require_license=not args.code_only)
    print(f"Scanned {len(paths)} current working-tree Git candidates (tracked + non-ignored new files).")
    for name, rule, line in findings:
        print(f"BLOCK {name}{':' + str(line) if line else ''}: {rule}")
    print(f"Manual review required for {len(review)} image/document/archive assets; no OCR/archive inspection performed.")
    print("Not checked: remote-only refs, actual credential rotation, rights approval, all secret formats, runtime security.")
    if not args.history:
        print("Local Git history not checked; use --history for a bounded, redacted scan.")
    if args.history:
        count, skipped, historical = check_history(root)
        print(f"History: inspected {count} reachable local blobs; skipped {skipped} blobs over 2 MB.")
        for oid, name, rule, line in historical:
            print(f"HISTORY BLOCK {oid} {name}:{line}: {rule}")
        if historical:
            findings.append(("history", "historical-material-needs-review", 0))
    if args.code_only:
        print("Development mode: main-project license gate skipped; do not treat this as publish-ready.")
    print("BLOCKED" if findings else "Limited automated checks passed; manual release gates remain.")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
