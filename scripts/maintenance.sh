#!/usr/bin/env bash
# maintenance.sh — Daily database maintenance (WAL checkpoint + optimize).
#
# Run via pvemonitor-maintenance.timer (daily at 03:07).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PVEMONITOR_HOME="${PVEMONITOR_HOME:-$PROJECT_ROOT}"

DB_PATH="$PVEMONITOR_HOME/runtime/db/metrics.sqlite3"

if [ ! -f "$DB_PATH" ]; then
    echo "Database not found at $DB_PATH — nothing to maintain."
    exit 0
fi

echo "==> PVEmonitor maintenance: $(date -Iseconds)"
echo "    DB: $DB_PATH"

# WAL checkpoint (truncate mode — resets WAL file to near-zero)
echo "    Running PRAGMA wal_checkpoint(TRUNCATE) ..."
sqlite3 "$DB_PATH" "PRAGMA wal_checkpoint(TRUNCATE);"

# Optimize (rebuilds indexes and updates query planner statistics)
echo "    Running PRAGMA optimize ..."
sqlite3 "$DB_PATH" "PRAGMA optimize;"

echo "==> Maintenance complete."
