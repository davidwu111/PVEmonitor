#!/usr/bin/env bash
# report-host-gpu.sh — Print recent GPU trend.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PVEMONITOR_HOME="${PVEMONITOR_HOME:-$PROJECT_ROOT}"

exec "$PVEMONITOR_HOME/.venv/bin/python" -m pvemonitor report-gpu "$@"
