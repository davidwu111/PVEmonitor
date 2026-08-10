#!/usr/bin/env bash
# debug-db.sh — Quick telemetry snapshot diagnostics (read-only).
# Usage: ./scripts/debug-db.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PVEMONITOR_HOME="${PVEMONITOR_HOME:-$PROJECT_ROOT}"
SNAPSHOT_DIR="$PVEMONITOR_HOME/runtime/exports/snapshots"
DB="$(ls -1t "$SNAPSHOT_DIR"/telemetry-*.sqlite3 2>/dev/null | head -n1 || true)"

PY="$PVEMONITOR_HOME/.venv/bin/python3"

echo "=== System ==="
echo "Time UTC: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "Epoch:    $(date +%s)"
if [ -z "$DB" ]; then
    echo "Snapshot: none found in $SNAPSHOT_DIR"
    echo "          Snapshots are written by \`pvemonitor serve\`."
    exit 0
fi
echo "Snapshot: $DB ($(stat -c%s "$DB" 2>/dev/null || echo '?') bytes)"

echo ""
echo "=== Table counts ==="
$PY -c "
import sqlite3
conn = sqlite3.connect('$DB')
for t in ['samples','host_metrics','guest_samples','guests','collector_errors']:
    n = conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
    print(f'  {t}: {n}')
conn.close()
"

echo ""
echo "=== Latest 3 samples ==="
$PY -c "
import sqlite3, time
conn = sqlite3.connect('$DB')
conn.row_factory = sqlite3.Row
now = int(time.time())
for r in conn.execute('SELECT id, ts, epoch_s FROM samples ORDER BY id DESC LIMIT 3').fetchall():
    age = now - r['epoch_s']
    print(f'  #{r[\"id\"]}  {r[\"ts\"]}  (age: {age}s = {age/60:.0f}m)')
conn.close()
"

echo ""
echo "=== Query: last 15min (JOIN) ==="
$PY -c "
import sqlite3
conn = sqlite3.connect('$DB')
n = conn.execute(\"SELECT COUNT(*) FROM samples s JOIN host_metrics h ON h.sample_id = s.id WHERE s.ts >= datetime('now', '-15m')\").fetchone()[0]
print(f'  Rows: {n}')
conn.close()
"

echo ""
echo "=== Query: last 24h (JOIN) ==="
$PY -c "
import sqlite3
conn = sqlite3.connect('$DB')
n = conn.execute(\"SELECT COUNT(*) FROM samples s JOIN host_metrics h ON h.sample_id = s.id WHERE s.ts >= datetime('now', '-24 hours')\").fetchone()[0]
print(f'  Rows: {n}')
conn.close()
"

echo ""
echo "=== PVEmonitor service ==="
systemctl is-active pvemonitor.service 2>&1 || echo "  (not active)"
systemctl is-enabled pvemonitor.service 2>&1 || echo "  (not enabled)"

echo ""
echo "=== Service last exit ==="
systemctl show pvemonitor.service -p ExecMainExitTimestamp 2>&1 || echo "  (no data)"
