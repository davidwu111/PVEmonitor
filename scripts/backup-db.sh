#!/usr/bin/env bash
# backup-db.sh — Back up the newest telemetry snapshot.
#
# Usage: ./scripts/backup-db.sh
# Copies the newest runtime/exports/snapshots/telemetry-*.sqlite3 into
# runtime/backups/ with a date stamp, keeping the last 7 backups.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PVEMONITOR_HOME="${PVEMONITOR_HOME:-$PROJECT_ROOT}"

SNAPSHOT_DIR="$PVEMONITOR_HOME/runtime/exports/snapshots"
BACKUP_DIR="$PVEMONITOR_HOME/runtime/backups"
STAMP="$(date +%Y%m%d)"

LATEST="$(ls -1t "$SNAPSHOT_DIR"/telemetry-*.sqlite3 2>/dev/null | head -n1 || true)"
if [ -z "$LATEST" ]; then
    echo "ERROR: No telemetry snapshot found in $SNAPSHOT_DIR"
    echo "       Snapshots are written by \`pvemonitor serve\` (see"
    echo "       storage.snapshot_interval_minutes in config/monitor.yaml)."
    exit 1
fi

mkdir -p "$BACKUP_DIR"

BACKUP_FILE="$BACKUP_DIR/metrics-$STAMP.sqlite3"

echo "==> Backing up newest snapshot to $BACKUP_FILE"
cp "$LATEST" "$BACKUP_FILE"

# Retention: keep last 7 daily backups
echo "==> Cleaning up old backups (keeping last 7)"
ls -1t "$BACKUP_DIR"/metrics-*.sqlite3 2>/dev/null | tail -n +8 | xargs -r rm -v

echo "==> Backup complete: $(ls -lh "$BACKUP_FILE" | awk '{print $5}')"
