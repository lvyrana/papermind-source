#!/bin/bash
# Scheduled backups include database, uploaded PDFs and figures, using one manifest.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec bash "$REPO_ROOT/scripts/backup_local.sh" --with-files
