"""Issue, extend or revoke a trial device without changing its saved papers."""
import argparse
from datetime import datetime, timedelta, timezone
import getpass
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "papermind"))
from trial_access import update


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("issue", "extend", "revoke"))
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()
    if not 1 <= args.days <= 365:
        parser.error("--days must be between 1 and 365")
    uid = str(uuid4()) if args.action == "issue" else getpass.getpass("Device UUID (hidden input): ")
    update(uid, datetime.now(timezone.utc) + timedelta(days=args.days), revoke=args.action == "revoke")
    if args.action == "issue":
        print(f"New private trial device: {uid}\nUse the service URL with ?uid=<this UUID>; share individually.")
    else:
        print("Trial admission updated; saved user data was not modified.")


if __name__ == "__main__":
    main()
