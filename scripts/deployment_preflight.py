"""Read-only runtime checks. Never import api or print configuration values."""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from uuid import UUID


def inspect(root, server=False):
    errors = []
    if sys.version_info[:2] != (3, 12):
        errors.append("Use Python 3.12 for the tested dependency lock")
    if platform.system() not in ("Linux", "Darwin"):
        errors.append("Backend file locks require Linux/macOS (Windows: use WSL2)")
    try:
        raw = subprocess.check_output(["node", "--version"], text=True).strip()
        major, minor, _ = map(int, raw.lstrip("v").split("."))
        if not ((major == 22 and minor >= 13) or major == 24):
            errors.append("Use Node 22.13+ (22.x) or Node 24.x LTS")
    except (OSError, ValueError, subprocess.CalledProcessError):
        errors.append("Node.js could not be checked")
    for name in ("papermind/requirements.lock", "web/package-lock.json"):
        if not (root / name).is_file():
            errors.append(f"Missing dependency lock: {name}")
    if server:
        path = root / "papermind/.env"
        if not path.is_file() or path.is_symlink():
            errors.append("Server needs a private regular papermind/.env file")
        else:
            if path.stat().st_mode & 0o077:
                errors.append("Set papermind/.env permissions to 600")
            # Parsing is deliberately limited to template-compatible KEY=value lines.
            values = {}
            for line in path.read_text().splitlines():
                match = re.match(r"^\s*([A-Z_]+)\s*=\s*(.*?)\s*$", line)
                if match:
                    values[match[1]] = match[2].strip("\"'")
            owner = os.environ.get("OWNER_UID", values.get("OWNER_UID", ""))
            if owner:
                try:
                    if str(UUID(owner)) != owner or UUID(owner).version != 4:
                        raise ValueError()
                except ValueError:
                    errors.append("OWNER_UID must be a canonical UUID v4; its value is not printed")
            for name in ("AI_MAX_CONCURRENT_REQUESTS", "GLOBAL_DAILY_AI_REQUEST_LIMIT", "UPLOAD_TOTAL_LIMIT_MB", "UPLOAD_USER_LIMIT_MB"):
                value = os.environ.get(name, values.get(name, "1"))
                if not value.isdigit() or int(value) < 1:
                    errors.append(f"{name} must be a positive integer")
            flag = os.environ.get("PAPERMIND_INVITE_ONLY", values.get("PAPERMIND_INVITE_ONLY", "false")).lower()
            if flag not in ("true", "false"):
                errors.append("PAPERMIND_INVITE_ONLY must be true or false")
            if flag == "true":
                store = root / "papermind/data/trial-access.json"
                try:
                    if store.is_symlink() or not isinstance(json.loads(store.read_text()), dict):
                        raise ValueError()
                except (OSError, ValueError):
                    errors.append("Invite-only enabled without a valid invitation store; clients will be denied")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", action="store_true")
    args = parser.parse_args()
    errors = inspect(Path(__file__).resolve().parents[1], args.server)
    for error in errors:
        print("BLOCK: " + error)
    if not errors:
        print("Runtime preflight passed. No service, configuration or data changed.")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
