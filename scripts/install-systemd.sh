#!/usr/bin/env bash
# install-systemd.sh — Install the PVEmonitor systemd service.
#
# Replaces @@PVEMONITOR_HOME@@ placeholders, copies the unit into
# /etc/systemd/system/, removes legacy units from previous versions,
# reloads systemd, and enables/starts the service.
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

SERVICE_UNIT="pvemonitor.service"

# Units from older PVEmonitor versions (removed during install)
LEGACY_UNITS=(
    "pvemonitor-collector.service"
    "pvemonitor-collector.timer"
    "pvemonitor-api.service"
    "pvemonitor-maintenance.service"
    "pvemonitor-maintenance.timer"
)

ENABLE_UNITS=true
if [ "${1:-}" = "--no-enable" ]; then
    ENABLE_UNITS=false
fi

echo "==> Installing PVEmonitor systemd service"
echo "    PVEMONITOR_HOME=$PVEMONITOR_HOME"
echo "    Destination: $SYSTEMD_DIR"

# Check we're root or have sudo
if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (sudo)."
    echo "    sudo $0"
    exit 1
fi

# Verify the unit file exists
if [ ! -f "$UNITS_DIR/$SERVICE_UNIT" ]; then
    echo "ERROR: Missing unit file: $UNITS_DIR/$SERVICE_UNIT"
    exit 1
fi

# Copy unit file with placeholder substitution
echo "==> Copying unit file (replacing @@PVEMONITOR_HOME@@) ..."
sed "s|@@PVEMONITOR_HOME@@|$PVEMONITOR_HOME|g" \
    "$UNITS_DIR/$SERVICE_UNIT" > "$SYSTEMD_DIR/$SERVICE_UNIT"
echo "    $SERVICE_UNIT"

# Stop, disable, and remove legacy units (safe on fresh installs too)
echo "==> Removing legacy units ..."
for unit in "${LEGACY_UNITS[@]}"; do
    systemctl stop "$unit" 2>/dev/null || true
    systemctl disable "$unit" 2>/dev/null || true
    rm -f "$SYSTEMD_DIR/$unit"
done

# Reload systemd
echo "==> Reloading systemd ..."
systemctl daemon-reload

if $ENABLE_UNITS; then
    echo "==> Enabling and starting the service ..."
    systemctl enable --now "$SERVICE_UNIT"
    echo ""
    echo "==> Service enabled and started."
else
    echo ""
    echo "==> Unit copied (not enabled). To start manually:"
    echo "    sudo systemctl enable --now $SERVICE_UNIT"
fi

echo ""
echo "==> Status check:"
echo ""
systemctl status "$SERVICE_UNIT" --no-pager -l 2>/dev/null || echo "    (service not active)"
echo ""
echo "==> Verify: curl http://localhost:8806/api/health"
