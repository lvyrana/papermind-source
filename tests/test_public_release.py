from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from check_public_release import check, check_history, inspect_file, private_path
from prepare_source_release import prepare


class PublicReleaseTests(unittest.TestCase):
    def test_public_deployment_guide_excludes_private_migration_receipts(self):
        from prepare_source_release import included
        self.assertTrue(included("docs/vercel-deployment.md"))
        self.assertFalse(included("docs/vercel-migration.md"))
        self.assertFalse(included("docs/vercel-migration-preview-record.md"))
        self.assertFalse(included("deploy/com.litfinch.cloud-backup.plist"))

    def test_only_reviewed_screenshots_are_exported(self):
        with tempfile.TemporaryDirectory() as directory:
            root, output = Path(directory) / "repo", Path(directory) / "export"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "LICENSE").write_text("Synthetic license fixture.\n")
            folder = root / "docs/screenshots"
            folder.mkdir(parents=True)
            allowed = "litfinch-welcome-20261022.png"
            for name in (allowed, "private.png", allowed + ".bak", "litfinch-welcome-20261023.png"):
                (folder / name).write_bytes(b"synthetic image fixture")
            prepare(root, output)
            self.assertEqual([p.name for p in (output / "docs/screenshots").iterdir()], [allowed])
            (folder / allowed).unlink()
            (folder / allowed).symlink_to(folder / "private.png")
            with self.assertRaises(ValueError):
                prepare(root, Path(directory) / "linked")

    def test_history_finds_removed_secret_without_printing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            token = "sk-" + "B" * 32
            (root / "old.txt").write_text(token)
            subprocess.run(["git", "-C", str(root), "add", "old.txt"], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                            "commit", "-qm", "synthetic fixture"], check=True)
            (root / "old.txt").unlink()
            _, _, findings = check_history(root)
            self.assertTrue(any(f[2] == "provider-key" for f in findings))
            self.assertNotIn(token, str(findings))

    def test_source_preparation_excludes_historical_screenshots_and_requires_license(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "web").mkdir()
            (root / "web/package.json").write_text("{}")
            (root / "docs/screenshots").mkdir(parents=True)
            (root / "docs/screenshots/private.png").write_bytes(b"fixture")
            target = Path(directory) / "prepared"
            with self.assertRaises(ValueError):
                prepare(root, target)
            prepare(root, target, draft=True)
            self.assertTrue((target / "web/package.json").exists())
            self.assertFalse((target / ".git").exists())
            self.assertFalse((target / "docs/screenshots/private.png").exists())
            self.assertTrue((target / "NOT-FOR-PUBLICATION.txt").exists())

    def test_licensed_source_export_preserves_license_and_draft_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            license_bytes = b"Synthetic license fixture, not a real authorization.\n"
            (root / "LICENSE").write_bytes(license_bytes)
            _, findings, _ = check(root)
            self.assertEqual(findings, [])
            for draft in (False, True):
                target = Path(directory) / ("draft" if draft else "source")
                prepare(root, target, draft=draft)
                self.assertEqual((target / "LICENSE").read_bytes(), license_bytes)
                manifest = json.loads((target / "SOURCE-MANIFEST.json").read_text())
                self.assertEqual(manifest["sha256"]["LICENSE"], hashlib.sha256(license_bytes).hexdigest())
                self.assertEqual(manifest["draft"], draft)
                self.assertEqual((target / "NOT-FOR-PUBLICATION.txt").exists(), draft)
                if draft:
                    self.assertIn("does not authorize publication", (target / "NOT-FOR-PUBLICATION.txt").read_text())

    def test_export_preserves_executable_scripts_without_unsafe_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "scripts").mkdir()
            script = root / "scripts/example.sh"
            script.write_text("#!/bin/sh\nexit 0\n")
            script.chmod(0o777)
            (root / "README.md").write_text("Synthetic export fixture.\n")
            target = Path(directory) / "source"
            prepare(root, target, draft=True)
            self.assertEqual((target / "scripts/example.sh").stat().st_mode & 0o777, 0o755)
            self.assertEqual((target / "README.md").stat().st_mode & 0o777, 0o644)
            manifest = json.loads((target / "SOURCE-MANIFEST.json").read_text())
            self.assertEqual(manifest["mode"]["scripts/example.sh"], "0755")
            self.assertEqual(manifest["mode"]["README.md"], "0644")

    def test_private_file_paths_are_blocked_but_example_is_allowed(self):
        for name in ("papermind/.env", "web/.env.production", "web/.env.local",
                     "papermind/data/paperdiary.db", "backup.db-wal", "backups/old.gz",
                     "软著申请/form.docx", "papermind-zotero/file.js", "private.pem"):
            self.assertTrue(private_path(name), name)
        self.assertFalse(private_path("web/.env.example"))
        self.assertFalse(private_path("papermind/src/config_store.py"))

    def test_demo_source_is_the_only_data_path_exception(self):
        self.assertFalse(private_path("web/src/data/demoPaper.js"))
        self.assertEqual(inspect_file("web/src/data/demoPaper.js", b"export const demo = {};\n"), [])
        for name in ("web/src/data/other.js", "web/src/data/demopaper.js",
                     "web/src/data/demoPaper.js.bak", "web/src/data/demoPaper.js.db",
                     "other/web/src/data/demoPaper.js", "backups/web/src/data/demoPaper.js",
                     "./web/src/data/demoPaper.js", "web/src/data/../data/demoPaper.js"):
            with self.subTest(path=name):
                self.assertTrue(private_path(name))

    def test_business_data_remains_blocked_in_every_data_directory(self):
        for name in ("data/demoPaper.js", "papermind/data/demoPaper.js", "web/data/demoPaper.js",
                     "papermind/data/users.json", "papermind/data/papers.csv",
                     "web/src/data/users.json", "web/src/data/notes.txt", "data/auth.sqlite3"):
            with self.subTest(path=name):
                self.assertEqual(inspect_file(name, b"synthetic business-data fixture\n"),
                                 [("private-path", 0)])

    def test_demo_source_still_checks_every_secret_pattern(self):
        fixtures = (
            ("private-key", "-----BEGIN " + "PRIVATE KEY-----"),
            ("provider-key", "sk-" + "A" * 32),
            ("github-token", "ghp_" + "B" * 32),
            ("aws-key-id", "AKIA" + "C" * 16),
        )
        for rule, token in fixtures:
            with self.subTest(rule=rule):
                issues = inspect_file("web/src/data/demoPaper.js", ("// fixture\n" + token).encode())
                self.assertEqual(issues, [(rule, 2)])
                self.assertNotIn(token, str(issues))

    def test_git_check_and_source_export_include_only_the_demo_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "LICENSE").write_text("Synthetic test license.\n")
            demo_name = "web/src/data/demoPaper.js"
            demo_bytes = b"export const demo = {};\n"
            demo = root / demo_name
            demo.parent.mkdir(parents=True)
            demo.write_bytes(demo_bytes)
            paths, findings, _ = check(root)
            self.assertIn(demo_name, paths)
            self.assertEqual(findings, [])
            business_paths = ("web/src/data/users.json", "papermind/data/papers.csv", "data/auth.sqlite3")
            for name in business_paths:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"synthetic business-data fixture\n")
            _, findings, _ = check(root)
            self.assertEqual(findings, [(name, "private-path", 0) for name in sorted(business_paths)])
            target = Path(directory) / "source"
            prepare(root, target)
            self.assertEqual((target / demo_name).read_bytes(), demo_bytes)
            self.assertFalse(any((target / name).exists() for name in business_paths))
            manifest = json.loads((target / "SOURCE-MANIFEST.json").read_text())
            self.assertEqual(manifest["sha256"][demo_name], hashlib.sha256(demo_bytes).hexdigest())
            self.assertTrue(all(name not in manifest["sha256"] for name in business_paths))

    def test_demo_source_exception_does_not_allow_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            demo = root / "web/src/data/demoPaper.js"
            demo.parent.mkdir(parents=True)
            demo.symlink_to("/path/that/must/not/be/read")
            _, findings, _ = check(root, require_license=False)
            self.assertEqual(findings, [("web/src/data/demoPaper.js", "symlink-needs-review", 0)])
            with self.assertRaisesRegex(ValueError, "Refusing non-regular source member"):
                prepare(root, Path(directory) / "source", draft=True)

    def test_patterns_report_positions_never_values(self):
        token = "sk-" + "A" * 32
        issues = inspect_file("example.py", ("# comment\n" + token).encode())
        self.assertEqual(issues, [("provider-key", 2)])
        self.assertNotIn(token, str(issues))

    def test_examples_must_leave_credentials_empty(self):
        self.assertEqual(inspect_file("web/.env.example", b"VITE_SENTRY_DSN=\nOWNER_UID=\n"), [])
        issues = inspect_file("papermind/.env.example", b'QWEN_API_KEY="do-not-ship-this"\n')
        self.assertEqual(issues, [("nonempty-example-credential", 1)])

    def test_git_candidates_include_new_files_and_tracked_ignored_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".gitignore").write_text(".env\n")
            (root / ".env").write_text("example only")
            (root / "main.py").write_text("pass\n")
            paths, findings, _ = check(root)
            self.assertNotIn(".env", paths)
            self.assertIn("main.py", paths)
            self.assertIn(("LICENSE", "main-project-license-not-confirmed", 0), findings)
            subprocess.run(["git", "-C", str(root), "add", "-f", ".env"], check=True)
            _, findings, _ = check(root, require_license=False)
            self.assertIn((".env", "private-path", 0), findings)

    def test_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "leak.txt").symlink_to("/path/that/must/not/be/read")
            _, findings, _ = check(root, require_license=False)
            self.assertIn(("leak.txt", "symlink-needs-review", 0), findings)


if __name__ == "__main__":
    unittest.main()
