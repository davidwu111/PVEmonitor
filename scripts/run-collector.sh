#!/usr/bin/env bash
# run-collector.sh — Run one PVEmonitor collection cycle.
#
# Usage: ./scripts/run-collector.sh [--force]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PVEMONITOR_HOME="${PVEMONITOR_HOME:-$PROJECT_ROOT}"

exec "$PVEMONITOR_HOME/.venv/bin/python" -m pvemonitor collect "$@"
