"""Export a private Connector source candidate, without Git history or local archives."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath

from check_public_release import check, inspect_file

ROOT_FILES = {"README.md", "CHANGELOG.md", "DECISIONS.md", "PRIVACY.md", "SECURITY.md",
              "SUPPORT.md", "LICENSE", ".gitignore", "manifest.json", "build.sh", "icon.svg",
              "bootstrap.js", "sidebar.js", "reader-context.js", "workspace.js", "account.js"}


def included(name):
    path = PurePosixPath(name)
    return (name in ROOT_FILES
            or len(path.parts) == 1 and path.name.startswith("test-") and path.suffix == ".mjs"
            or path.parts[0] in {"scripts", ".github"} and path.suffix in {".mjs", ".yml", ".yaml"}
            or path.parts[0] == "locale" and path.suffix == ".ftl")


def prepare(root, target):
    if target.exists() or target.is_symlink():
        raise ValueError("Output must be a new directory")
    paths, _, _ = check(root)
    selected = [name for name in paths if included(name)]
    if not {"LICENSE", "manifest.json", "build.sh", "bootstrap.js"}.issubset(selected):
        raise ValueError("Connector license, manifest, build script and bootstrap are required")
    content, modes = {}, {}
    for name in selected:
        source = root / name
        if source.is_symlink() or not source.is_file() or any(
            (root / parent).is_symlink() for parent in PurePosixPath(name).parents if str(parent) != "."
        ):
            raise ValueError(f"Refusing linked or non-regular source: {name}")
        data = source.read_bytes()
        if inspect_file(name, data):
            raise ValueError(f"Source needs review: {name}")
        content[name] = data
        modes[name] = 0o755 if source.stat().st_mode & 0o111 else 0o644
    if not content["LICENSE"].strip():
        raise ValueError("Connector LICENSE is empty")
    manifest = json.loads(content["manifest.json"])
    if manifest["applications"]["zotero"]["id"] != "papermind-connector@papermind.local":
        raise ValueError("Existing plugin installation identity must be preserved")
    target.mkdir(parents=True, mode=0o700)
    for name, data in content.items():
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        destination.chmod(modes[name])
    (target / "SOURCE-MANIFEST.json").write_text(json.dumps({
        "draft": True, "version": manifest["version"],
        "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in content.items()},
        "mode": {name: format(mode, "04o") for name, mode in modes.items()},
    }, indent=2) + "\n")
    (target / "NOT-FOR-PUBLICATION.txt").write_text(
        "Private source review candidate. No Git history, old XPI, trial archives or user data copied.\n"
        "Build an XPI from this source and review compatibility before approving publication.\n"
    )
    return len(selected), len(paths) - len(selected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "papermind-zotero"
    try:
        count, excluded = prepare(root, args.output)
    except (KeyError, ValueError, OSError) as error:
        parser.exit(1, f"Preparation blocked: {error}\n")
    print(f"Prepared {count} Connector source files; excluded {excluded} local files. Private review only.")


if __name__ == "__main__":
    main()
