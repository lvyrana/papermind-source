"""Copy a minimal source snapshot with no Git history or old screenshots."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json

from check_public_release import check, inspect_file, private_path

ROOT_FILES = {"README.md", "CHANGELOG.md", "DECISIONS.md", "THIRD_PARTY_NOTICES.md",
              "LICENSE", "LICENSE.md", "LICENSE.txt", ".gitignore", ".gitattributes"}
PUBLIC_DOCS = {"docs/trial-guide.md", "docs/deep-reading-overview.md", "docs/chinese-reading-validation.md",
               "docs/open-source-readiness.md", "docs/deployment-recovery.md"}
ROOTS = {"web", "papermind", "scripts", "tests", "deploy", ".github", "zotero-plugin"}


def included(name):
    path = PurePosixPath(name)
    if private_path(name):
        return False
    return name in ROOT_FILES or name in PUBLIC_DOCS or path.parts[0] in ROOTS


def prepare(root, target, draft=False):
    paths, _, _ = check(root, require_license=False)
    selected = [name for name in paths if included(name)]
    if not draft and not any(name in selected and not (root / name).is_symlink()
                             and (root / name).is_file() and (root / name).read_text().strip()
                             for name in ("LICENSE", "LICENSE.md", "LICENSE.txt")):
        raise ValueError("Main project LICENSE is not confirmed; use --draft for local preparation only")
    if target.exists() or target.is_symlink():
        raise ValueError("Output must be a new directory")
    contents = {}
    modes = {}
    for name in selected:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Refusing non-regular source member: {name}")
        if any((root / parent).is_symlink() for parent in PurePosixPath(name).parents if str(parent) != "."):
            raise ValueError("Refusing linked source directory")
        data = path.read_bytes()
        if inspect_file(name, data):
            raise ValueError(f"Source member needs review: {name}")
        contents[name] = data
        # Keep executable entry points, without carrying writable or special bits.
        modes[name] = 0o755 if path.stat().st_mode & 0o111 else 0o644
    target.mkdir(parents=True, mode=0o700)
    manifest = {}
    for name, data in contents.items():
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        destination.chmod(modes[name])
        manifest[name] = hashlib.sha256(data).hexdigest()
    (target / "SOURCE-MANIFEST.json").write_text(json.dumps({
        "draft": draft, "sha256": manifest,
        "mode": {name: format(mode, "04o") for name, mode in modes.items()},
    }, indent=2) + "\n")
    if draft:
        (target / "NOT-FOR-PUBLICATION.txt").write_text(
            "Private review only. A license file does not authorize publication.\n"
            "Review sensitive files, historical credential rotation and publication approval before release.\n"
        )
    return len(selected), len(paths) - len(selected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--draft", action="store_true")
    args = parser.parse_args()
    try:
        selected, excluded = prepare(Path(__file__).resolve().parents[1], args.output, args.draft)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Preparation blocked: {error}\n")
    print(f"Prepared {selected} source files; excluded {excluded} working-tree files. No Git history copied.")


if __name__ == "__main__":
    main()
