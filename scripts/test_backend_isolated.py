"""Run backend tests on a disposable source copy, never the maintainer's data/."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="papermind-tests-") as directory:
        target = Path(directory)
        # Explicit source-only selection: no .env, databases, PDFs, or venvs.
        for folder in ("papermind", "papermind/src", "tests", "scripts"):
            (target / folder).mkdir(parents=True, exist_ok=True)
            for source in (root / folder).glob("*.py"):
                if source.is_symlink():
                    raise RuntimeError(f"Refusing symlink: {source.relative_to(root)}")
                shutil.copy2(source, target / folder / source.name)
        (target / "zotero-plugin").mkdir()
        for name in ("manifest.json", "papermind-connector.xpi"):
            shutil.copy2(root / "zotero-plugin" / name, target / "zotero-plugin" / name)
        env = dict(os.environ)
        # Never let developer cloud credentials turn an isolated test into a
        # production database test. Explicit cloud tests inject synthetic env.
        for key in list(env):
            if key in {"PAPERMIND_SQL_BACKEND", "PAPERMIND_FILE_BACKEND", "BLOB_STORE_ID", "BLOB_READ_WRITE_TOKEN", "VERCEL_OIDC_TOKEN"} or key.startswith(("PAPERMIND_LIBRARY_", "PAPERMIND_AUTH_", "PAPERMIND_RUNTIME_")):
                env.pop(key)
        for key in ("QWEN_API_KEY", "GLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
                    "SENTRY_DSN", "OWNER_UID", "ZOTERO_OAUTH_CLIENT_KEY", "ZOTERO_OAUTH_CLIENT_SECRET", "PAPERMIND_PUBLIC_ORIGIN"):
            env[key] = ""
        # Source files and database initialization resolve within this temp copy.
        env.pop("PYTHONPATH", None)
        for key in ("PAPERMIND_INVITE_ONLY", "GLOBAL_DAILY_AI_REQUEST_LIMIT", "AI_MAX_CONCURRENT_REQUESTS",
                    "UPLOAD_TOTAL_LIMIT_MB", "UPLOAD_USER_LIMIT_MB"):
            env.pop(key, None)
        print("Backend tests: disposable source/data copy; provider keys disabled.", flush=True)
        return subprocess.call(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", *sys.argv[1:]],
            cwd=target, env=env,
        )


if __name__ == "__main__":
    raise SystemExit(main())
