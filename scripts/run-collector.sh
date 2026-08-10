#!/usr/bin/env bash
# run-collector.sh — Run one debug collection cycle.
#
# Usage: ./scripts/run-collector.sh [--force]
#
# NOTE: This runs a one-shot collection into a throwaway store and prints a
# summary. The live service (pvemonitor.service) collects automatically.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PVEMONITOR_HOME="${PVEMONITOR_HOME:-$PROJECT_ROOT}"

exec "$PVEMONITOR_HOME/.venv/bin/python" -m pvemonitor collect "$@"
