"""Collect installed dependency notices, without credentials or application data.

Run with the locked backend venv after npm ci. This is an inventory, not a legal
compatibility decision. Absent optional npm packages are listed, not fabricated.
"""
import argparse
from importlib import metadata
import json
from pathlib import Path
import re
import shutil

NOTICE = re.compile(r"^(?:(?:third[._-]party|thirdparty)[._-])?(licen[sc]e|copying|copyright|notice)([.\-_]|$)", re.I)


def collect(root, output):
    root, output = Path(root).resolve(), Path(output)
    if output.exists():
        raise ValueError("Notice destination must be a new directory")
    records = []
    sources = []
    lock = json.loads((root / "web/package-lock.json").read_text())
    for location, package in lock["packages"].items():
        if not location or "node_modules/" not in location:
            continue
        directory = root / "web" / location
        name = location.rsplit("node_modules/", 1)[-1]
        texts = [p for p in directory.glob("*") if p.is_file() and not p.is_symlink() and NOTICE.match(p.name)]
        if not texts:
            # Some packages distribute the full permission text in their README.
            # A bare SPDX name or link is deliberately not treated as a full notice.
            for readme in directory.glob("README*"):
                if readme.is_file() and not readme.is_symlink():
                    content = readme.read_text(errors="replace")
                    if "Permission is hereby granted" in content or "Redistribution and use in source and binary forms" in content:
                        texts.append(readme)
        record = {"ecosystem": "npm", "name": name, "version": package.get("version"),
                  "license": package.get("license", "UNKNOWN"), "development": package.get("dev", False),
                  "installed": directory.is_dir(), "notices": []}
        for source in texts:
            target = Path("npm") / location / source.name
            sources.append((source, target))
            record["notices"].append(target.as_posix())
        records.append(record)
    packages = re.findall(r"^([a-zA-Z0-9_.-]+)==([^\s\\]+)", (root / "papermind/requirements.lock").read_text(), re.M)
    for name, version in packages:
        distribution = metadata.distribution(name)
        if distribution.version != version:
            raise ValueError(f"Installed version does not match Python lock: {name}")
        record = {"ecosystem": "python", "name": name, "version": version, "installed": True,
                  "license": distribution.metadata.get("License-Expression") or (distribution.metadata.get("License") or "UNKNOWN").splitlines()[0], "notices": []}
        for file in distribution.files or []:
            if NOTICE.match(Path(str(file)).name):
                source = Path(distribution.locate_file(file)).resolve()
                if source.is_file():
                    target = Path("python") / name / Path(str(file)).name
                    # Distinct copyright files with identical names retain their paths.
                    target = target.parent / str(len(record["notices"])) / target.name
                    sources.append((source, target))
                    record["notices"].append(target.as_posix())
        records.append(record)
    output.mkdir(parents=True, mode=0o700)
    for source, relative in sources:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    (output / "inventory.json").write_text(json.dumps(records, indent=2) + "\n")
    missing = [f"{r['ecosystem']}:{r['name']}@{r['version']}" for r in records if r["installed"] and not r["notices"]]
    absent = [r["name"] for r in records if not r["installed"]]
    (output / "REVIEW.md").write_text(
        "# Third-party notice inventory\n\nInstalled dependency notices only; review binary internals, external fonts and Connector separately.\n\n"
        + "Installed packages without a notice file:\n\n" + ("\n".join("- " + name for name in missing) or "None")
        + "\n\nUninstalled optional/platform packages (not distributed here):\n\n"
        + ("\n".join("- " + name for name in absent) or "None") + "\n")
    return {"packages": len(records), "notice_files": len(sources), "missing_notices": missing, "uninstalled_platform_packages": len(absent)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(collect(Path(__file__).resolve().parents[1], args.output), indent=2))


if __name__ == "__main__":
    main()
