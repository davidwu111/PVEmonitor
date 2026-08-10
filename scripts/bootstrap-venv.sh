#!/usr/bin/env bash
# bootstrap-venv.sh — Create Python virtual environment and install dependencies.
#
# Usage: ./scripts/bootstrap-venv.sh [--dev]
#   --dev   Also install dev dependencies (pytest)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export PVEMONITOR_HOME="${PVEMONITOR_HOME:-$PROJECT_ROOT}"

cd "$PVEMONITOR_HOME"

echo "==> Bootstrapping PVEmonitor environment"
echo "    PVEMONITOR_HOME=$PVEMONITOR_HOME"

# ─── Find Python ───────────────────────────────────────────────────────
PYTHON="$(command -v python3 || command -v python)"
echo "    Python: $($PYTHON --version)"

PY_VER="$($PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if ! $PYTHON -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    echo "ERROR: Python 3.11+ required, found $PY_VER"
    exit 1
fi

# ─── Pre-flight: ensure venv module works ──────────────────────────────
# On Debian/Proxmox, python3-venv may not be installed. The venv module
# will exist but fail at runtime because ensurepip is missing.
if ! $PYTHON -c 'import ensurepip' 2>/dev/null; then
    echo ""
    echo "ERROR: The Python venv module is not fully installed."
    echo "       ensurepip is missing — usually this means python3-venv"
    echo "       is not installed."
    echo ""
    echo "       On Debian/Proxmox:"
    echo "         sudo apt install python3.13-venv"
    echo ""
    echo "       Then re-run this script."
    exit 1
fi

# ─── Create or repair virtual environment ──────────────────────────────
NEED_CREATE=false
if [ ! -d ".venv" ]; then
    NEED_CREATE=true
elif [ ! -f ".venv/bin/pip" ]; then
    echo ""
    echo "==> Existing .venv/ is broken (no pip found — likely created without"
    echo "    python3-venv installed). Removing and recreating..."
    rm -rf .venv
    NEED_CREATE=true
fi

if $NEED_CREATE; then
    echo "==> Creating virtual environment at .venv/"
    $PYTHON -m venv .venv

    # Verify venv was created correctly
    if [ ! -f ".venv/bin/pip" ]; then
        echo "ERROR: Virtual environment created but pip is still missing."
        echo "       Try: sudo apt install python3.13-venv"
        echo "       Then: rm -rf .venv && ./scripts/bootstrap-venv.sh"
        exit 1
    fi
else
    echo "==> Virtual environment already exists at .venv/"
fi

# ─── Upgrade pip ───────────────────────────────────────────────────────
echo "==> Upgrading pip ..."
.venv/bin/pip install --upgrade pip

# ─── Install base dependencies ─────────────────────────────────────────
echo "==> Installing base dependencies"
.venv/bin/pip install -r requirements/base.txt

# ─── Install dev dependencies if requested ─────────────────────────────
if [ "${1:-}" = "--dev" ]; then
    echo "==> Installing dev dependencies"
    .venv/bin/pip install -r requirements/dev.txt
fi

# ─── Editable install of pvemonitor ────────────────────────────────────
echo "==> Installing pvemonitor (editable)"
.venv/bin/pip install -e .

# ─── Verify installation ───────────────────────────────────────────────
echo ""
echo "==> Verifying installation ..."
if .venv/bin/python -c 'import pvemonitor' 2>/dev/null; then
    VER="$(.venv/bin/python -c 'import pvemonitor; print(pvemonitor.__version__)')"
    echo "    pvemonitor v$VER — OK"
else
    echo "ERROR: pvemonitor module could not be imported."
    echo "       Check the pip output above for errors."
    exit 1
fi

echo ""
echo "==> Bootstrap complete!"
echo "    Run:    $PVEMONITOR_HOME/.venv/bin/python -m pvemonitor --help"
echo "    Serve:  $PVEMONITOR_HOME/.venv/bin/python -m pvemonitor serve"
