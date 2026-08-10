#!/usr/bin/env bash
# uninstall-systemd.sh — Remove PVEmonitor systemd units from the PVE host.
#
# Stops services, disables units, removes unit files from /etc/systemd/system/,
# and optionally removes the entire project directory.
#
# Usage:
#   sudo ./scripts/uninstall-systemd.sh               # remove units only
#   sudo ./scripts/uninstall-systemd.sh --purge        # also delete project root
#   sudo ./scripts/uninstall-systemd.sh --dry-run      # show what would be removed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PVEMONITOR_HOME="${PVEMONITOR_HOME:-$PROJECT_ROOT}"

SYSTEMD_DIR="/etc/systemd/system"

UNITS=(
    "pvemonitor.service"
    "pvemonitor-collector.service"
    "pvemonitor-collector.timer"
    "pvemonitor-api.service"
    "pvemonitor-maintenance.service"
    "pvemonitor-maintenance.timer"
)

DRY_RUN=false
PURGE=false

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --purge)   PURGE=true ;;
        *)         echo "Unknown option: $arg"; exit 1 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (sudo)."
    echo "    sudo $0"
    exit 1
fi

echo "==> PVEmonitor systemd uninstall"
echo "    PVEMONITOR_HOME=$PVEMONITOR_HOME"
if $DRY_RUN; then
    echo "    DRY RUN — no changes will be made"
fi

# ─── 1. Stop services ──────────────────────────────────────────────────
echo ""
echo "==> Stopping services ..."
for unit in "${UNITS[@]}"; do
    if systemctl is-active --quiet "$unit" 2>/dev/null; then
        echo "    Stopping $unit"
        $DRY_RUN || systemctl stop "$unit" 2>/dev/null || true
    else
        echo "    $unit — not active, skipping"
    fi
done

# ─── 2. Disable units ──────────────────────────────────────────────────
echo ""
echo "==> Disabling units ..."
for unit in "${UNITS[@]}"; do
    if systemctl is-enabled --quiet "$unit" 2>/dev/null; then
        echo "    Disabling $unit"
        $DRY_RUN || systemctl disable "$unit" 2>/dev/null || true
    else
        echo "    $unit — not enabled, skipping"
    fi
done

# ─── 3. Remove unit files ──────────────────────────────────────────────
echo ""
echo "==> Removing unit files from $SYSTEMD_DIR ..."
for unit in "${UNITS[@]}"; do
    if [ -f "$SYSTEMD_DIR/$unit" ]; then
        echo "    Removing $unit"
        $DRY_RUN || rm -f "$SYSTEMD_DIR/$unit"
    else
        echo "    $unit — not found, skipping"
    fi
done

# ─── 4. Reload systemd ─────────────────────────────────────────────────
echo ""
echo "==> Reloading systemd ..."
$DRY_RUN || systemctl daemon-reload

# ─── 5. Reset failed state (clean up any lingering state) ──────────────
echo ""
echo "==> Resetting any failed unit state ..."
for unit in "${UNITS[@]}"; do
    $DRY_RUN || systemctl reset-failed "$unit" 2>/dev/null || true
done

# ─── 6. Optional: purge project directory ──────────────────────────────
if $PURGE; then
    echo ""
    echo "==> Purging project directory: $PVEMONITOR_HOME"
    if $DRY_RUN; then
        echo "    (would delete entire project directory)"
    else
        read -r -p "    Delete $PVEMONITOR_HOME and all data? [y/N] " CONFIRM
        if [ "$CONFIRM" = "y" ] || [ "$CONFIRM" = "Y" ]; then
            rm -rf "$PVEMONITOR_HOME"
            echo "    Project directory removed."
        else
            echo "    Skipped. Project directory kept at $PVEMONITOR_HOME"
        fi
    fi
fi

# ─── Summary ────────────────────────────────────────────────────────────
echo ""
echo "==> Uninstall complete."
echo ""
echo "    Systemd units have been removed."
if ! $PURGE; then
    echo "    Project directory kept at: $PVEMONITOR_HOME"
    echo "    Telemetry snapshots and logs are preserved."
    echo ""
    echo "    To also delete the project and all data:"
    echo "        sudo $0 --purge"
fi
