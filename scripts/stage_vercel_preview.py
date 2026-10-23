"""Stage a source-only business preview in a NEW directory, excluding all data."""
import argparse
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_ASSETS = {".html", ".js", ".mjs", ".css", ".svg", ".png", ".jpg", ".jpeg",
                  ".webp", ".ico", ".woff", ".woff2", ".ttf", ".json", ".txt"}


def copy_file(source, destination):
    if source.is_symlink() or not source.is_file():
        raise ValueError("Only regular reviewed source/build files may be staged")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def stage(destination):
    if destination.exists():
        raise ValueError("Use a new staging directory")
    if not (ROOT / "web/dist/index.html").is_file():
        raise ValueError("Build the frontend before staging")
    assets = []
    for source in (ROOT / "web/dist").rglob("*"):
        if source.is_dir() and not source.is_symlink():
            continue
        if source.name == ".DS_Store" and not source.is_symlink():
            continue
        if source.is_symlink() or source.suffix not in ALLOWED_ASSETS:
            raise ValueError("Unexpected frontend build asset type")
        assets.append(source)
    destination.mkdir(mode=0o700, parents=False)
    for name in ("app.py", "vercel.json", ".vercelignore", "requirements.txt", "package.json", "package-lock.json", "api/file-grant.mjs"):
        copy_file(ROOT / "deploy/vercel-preview" / name, destination / name)
    copy_file(ROOT / "deploy/vercel-probe/.python-version", destination / ".python-version")
    for folder in ("papermind", "papermind/src"):
        for source in (ROOT / folder).glob("*.py"):
            copy_file(source, destination / folder / source.name)
    for source in assets:
        copy_file(source, destination / "web/dist" / source.relative_to(ROOT / "web/dist"))
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    print(stage(parser.parse_args().destination))
