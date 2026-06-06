#!/usr/bin/env bash
# healthcheck.sh — PVEmonitor health check.
#
# Exits 0 if healthy, 1 if degraded, 2 if dead.
# Pass --json for JSON output.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PVEMONITOR_HOME="${PVEMONITOR_HOME:-$PROJECT_ROOT}"

exec "$PVEMONITOR_HOME/.venv/bin/python" -m pvemonitor health "$@"
