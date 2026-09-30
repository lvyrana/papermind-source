#!/bin/bash
# Private, verified SQLite + PDF + figure snapshots. Existing backups are retained.
set -euo pipefail
umask 077
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mode="${1:---with-files}"
args=()
case "$mode" in
    --with-files|--with-pdfs) ;;
    database|--database-only) args+=(--database-only) ;;
    *) echo "用法：bash scripts/backup_local.sh [--with-files|--database-only]"; exit 2 ;;
esac
exec "${PAPERMIND_PYTHON:-python3}" "$REPO_ROOT/scripts/backup_data.py" create \
    --data-dir "${PAPERMIND_DATA_DIR:-$REPO_ROOT/papermind/data}" \
    --backup-dir "${BACKUP_DIR:-$REPO_ROOT/backups}" "${args[@]}"
