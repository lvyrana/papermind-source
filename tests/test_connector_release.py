import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_connector_release import prepare


class ConnectorReleaseTests(unittest.TestCase):
    def test_notices_and_reviewed_screenshot_survive_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root, output = Path(directory) / "repo", Path(directory) / "candidate"
            self.fixture(root)
            (root / "THIRD_PARTY_NOTICES.md").write_text("Synthetic notice.\n")
            (root / "docs/screenshots").mkdir(parents=True)
            for name in ("reading-card-20261022.png", "private.png", "reading-card-20261022.png.bak"):
                (root / "docs/screenshots" / name).write_bytes(b"synthetic image fixture")
            prepare(root, output)
            self.assertTrue((output / "THIRD_PARTY_NOTICES.md").exists())
            self.assertEqual([p.name for p in (output / "docs/screenshots").iterdir()],
                             ["reading-card-20261022.png"])

    def fixture(self, root):
        root.mkdir()
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / "LICENSE").write_text("Synthetic test license.\n")
        (root / "manifest.json").write_text(json.dumps({"version": "0.7.0", "applications": {
            "zotero": {"id": "papermind-connector@papermind.local"}}}))
        (root / "bootstrap.js").write_text("// Synthetic test source.\n")
        (root / "build.sh").write_text("#!/bin/sh\nexit 0\n")
        (root / "build.sh").chmod(0o777)

    def test_export_excludes_archives_data_and_history_and_preserves_scripts(self):
        with tempfile.TemporaryDirectory() as directory:
            root, output = Path(directory) / "repo", Path(directory) / "candidate"
            self.fixture(root)
            for name in ("old-trial.zip", "old.xpi", ".env", "private.pdf", "notes.db"):
                (root / name).write_bytes(b"private fixture")
            count, excluded = prepare(root, output)
            self.assertEqual((count, excluded), (4, 5))
            self.assertFalse((output / ".git").exists())
            self.assertFalse(any((output / name).exists() for name in ("old-trial.zip", "old.xpi", ".env", "private.pdf", "notes.db")))
            self.assertEqual((output / "build.sh").stat().st_mode & 0o777, 0o755)
            self.assertTrue((output / "NOT-FOR-PUBLICATION.txt").exists())

    def test_secret_in_selected_source_blocks_export_without_printing_value(self):
        with tempfile.TemporaryDirectory() as directory:
            root, output = Path(directory) / "repo", Path(directory) / "candidate"
            self.fixture(root)
            secret = "sk-" + "B" * 32
            (root / "bootstrap.js").write_text(secret)
            with self.assertRaises(ValueError) as error:
                prepare(root, output)
            self.assertNotIn(secret, str(error.exception))
            self.assertFalse(output.exists())

    def test_symlink_in_selected_source_blocks_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root, output = Path(directory) / "repo", Path(directory) / "candidate"
            self.fixture(root)
            (root / "workspace.js").symlink_to(root / "bootstrap.js")
            with self.assertRaises(ValueError):
                prepare(root, output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
