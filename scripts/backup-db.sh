#!/usr/bin/env bash
# backup-db.sh — Create a backup of the SQLite database.
#
# Usage: ./scripts/backup-db.sh
# Backups are stored in runtime/backups/ with date stamp.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PVEMONITOR_HOME="${PVEMONITOR_HOME:-$PROJECT_ROOT}"

DB_PATH="$PVEMONITOR_HOME/runtime/db/metrics.sqlite3"
BACKUP_DIR="$PVEMONITOR_HOME/runtime/backups"
STAMP="$(date +%Y%m%d)"

if [ ! -f "$DB_PATH" ]; then
    echo "ERROR: Database not found at $DB_PATH"
    exit 1
fi

mkdir -p "$BACKUP_DIR"

BACKUP_FILE="$BACKUP_DIR/metrics-$STAMP.sqlite3"

echo "==> Backing up $DB_PATH to $BACKUP_FILE"
sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"

# Retention: keep last 7 daily backups
echo "==> Cleaning up old backups (keeping last 7)"
ls -1t "$BACKUP_DIR"/metrics-*.sqlite3 2>/dev/null | tail -n +8 | xargs -r rm -v

echo "==> Backup complete: $(ls -lh "$BACKUP_FILE" | awk '{print $5}')"
