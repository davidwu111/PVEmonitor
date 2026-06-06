#!/usr/bin/env bash
# bootstrap-venv.sh — Create Python virtual environment and install dependencies.
#
# Usage: ./scripts/bootstrap-venv.sh [--dev]
#   --dev   Also install dev dependencies (pytest)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Determine PVEMONITOR_HOME
export PVEMONITOR_HOME="${PVEMONITOR_HOME:-$PROJECT_ROOT}"

cd "$PVEMONITOR_HOME"

echo "==> Bootstrapping PVEmonitor environment"
echo "    PVEMONITOR_HOME=$PVEMONITOR_HOME"

# Check Python
PYTHON="$(command -v python3 || command -v python)"
echo "    Python: $($PYTHON --version)"

# Check Python version >= 3.11
PY_VER="$($PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if ! $PYTHON -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "ERROR: Python 3.11+ required, found $PY_VER"
    exit 1
fi

# Create virtual environment
if [ ! -d ".venv" ]; then
    echo "==> Creating virtual environment at .venv/"
    $PYTHON -m venv .venv
else
    echo "==> Virtual environment already exists at .venv/"
fi

# Upgrade pip
.venv/bin/pip install --upgrade pip -q

# Install base dependencies
echo "==> Installing base dependencies"
.venv/bin/pip install -r requirements/base.txt

# Install dev dependencies if requested
INSTALL_DEV=false
if [ "${1:-}" = "--dev" ]; then
    INSTALL_DEV=true
fi

if $INSTALL_DEV; then
    echo "==> Installing dev dependencies"
    .venv/bin/pip install -r requirements/dev.txt
fi

# Editable install of pvemonitor
echo "==> Installing pvemonitor (editable)"
.venv/bin/pip install -e . -q

echo ""
echo "==> Bootstrap complete!"
echo "    Run: $PVEMONITOR_HOME/.venv/bin/python -m pvemonitor --help"
echo "    Init DB: $PVEMONITOR_HOME/.venv/bin/python -m pvemonitor init-db"
