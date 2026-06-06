#!/usr/bin/env bash
# install-systemd.sh — Install PVEmonitor systemd units onto the PVE host.
#
# Replaces @@PVEMONITOR_HOME@@ placeholders in the unit files, copies them
# into /etc/systemd/system/, reloads systemd, and enables the timer + API.
#
# Safe to run repeatedly (idempotent).
#
# Usage:
#   sudo ./scripts/install-systemd.sh
#   sudo ./scripts/install-systemd.sh --no-enable   # copy only, don't start

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PVEMONITOR_HOME="${PVEMONITOR_HOME:-$PROJECT_ROOT}"

SYSTEMD_DIR="/etc/systemd/system"
UNITS_DIR="$PVEMONITOR_HOME/systemd"

ENABLE_UNITS=true
if [ "${1:-}" = "--no-enable" ]; then
    ENABLE_UNITS=false
fi

echo "==> Installing PVEmonitor systemd units"
echo "    PVEMONITOR_HOME=$PVEMONITOR_HOME"
echo "    Destination: $SYSTEMD_DIR"

# Check we're root or have sudo
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (sudo)."
    echo "    sudo $0"
    exit 1
fi

# Verify unit files exist
REQUIRED_FILES=(
    "pvemonitor-collector.service"
    "pvemonitor-collector.timer"
    "pvemonitor-api.service"
    "pvemonitor-maintenance.service"
    "pvemonitor-maintenance.timer"
)
for f in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$UNITS_DIR/$f" ]; then
        echo "ERROR: Missing unit file: $UNITS_DIR/$f"
        exit 1
    fi
done

# Copy unit files with placeholder substitution
echo "==> Copying unit files (replacing @@PVEMONITOR_HOME@@) ..."
for f in "${REQUIRED_FILES[@]}"; do
    sed "s|@@PVEMONITOR_HOME@@|$PVEMONITOR_HOME|g" \
        "$UNITS_DIR/$f" > "$SYSTEMD_DIR/$f"
    echo "    $f"
done

# Reload systemd
echo "==> Reloading systemd ..."
systemctl daemon-reload

if $ENABLE_UNITS; then
    echo "==> Enabling and starting units ..."

    # Stop any existing instances first (clean slate)
    systemctl stop pvemonitor-collector.timer 2>/dev/null || true
    systemctl stop pvemonitor-collector.service 2>/dev/null || true
    systemctl stop pvemonitor-api.service 2>/dev/null || true

    # Enable and start
    systemctl enable --now pvemonitor-collector.timer
    systemctl enable --now pvemonitor-api.service
    systemctl enable --now pvemonitor-maintenance.timer

    echo ""
    echo "==> Units enabled and started."
else
    echo ""
    echo "==> Units copied (not enabled). To start manually:"
    echo "    sudo systemctl enable --now pvemonitor-collector.timer"
    echo "    sudo systemctl enable --now pvemonitor-api.service"
    echo "    sudo systemctl enable --now pvemonitor-maintenance.timer"
fi

echo ""
echo "==> Status check:"
echo ""
systemctl status pvemonitor-collector.timer --no-pager -l 2>/dev/null || echo "    (timer not active)"
echo ""
systemctl status pvemonitor-api.service --no-pager -l 2>/dev/null || echo "    (API not active)"
echo ""
echo "==> Verify: curl http://localhost:8806/api/health"
