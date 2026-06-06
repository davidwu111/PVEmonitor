"""Tests for guest state handling and metrics building."""

import pytest
from pvemonitor.guests import build_guest_metrics


class TestBuildGuestMetrics:
    """Test that guest_metrics are built correctly for various guest states."""

    def test_running_qemu_with_details(self):
        """Running QEMU guest with detail data should populate runtime fields."""
        inventory = {
            "vmid": 100,
            "type": "qemu",
            "name": "test-vm",
            "status": "running",
            "is_running": 1,
            "cpu": 2.5,
            "maxcpu": 8,
            "mem": 8589934,  # in KiB from pvesh
            "maxmem": 16777216,
            "uptime": 3600,
            "diskread": 107374182400,
            "diskwrite": 53687091200,
            "netin": 214748364800,
            "netout": 107374182400,
        }
        details = {
            "uptime": 3605,
            "cpus": 8,
            "maxmem": 17179869184,
            "mem": 8589934592,
            "cpu": 2.5,
            "diskread": 107374182500,
            "diskwrite": 53687091300,
            "netin": 214748364900,
            "netout": 107374182500,
            "agent": 1,
            "freemem": 4294967296,
            "ballooninfo": {"actual": 8589934592},
        }

        metrics = build_guest_metrics(inventory, details)

        assert metrics["name"] == "test-vm"
        assert metrics["status"] == "running"
        assert metrics["is_running"] == 1
        assert metrics["uptime_s"] == 3605
        assert metrics["maxcpu"] == 8
        assert metrics["cpu_host_pct"] == 250.0  # 2.5 * 100
        assert metrics["cpu_of_allocated_pct"] == 31.25  # (2.5 / 8) * 100
        assert metrics["mem_bytes"] == 8589934592
        assert metrics["maxmem_bytes"] == 17179869184
        assert metrics["mem_pct"] == 50.0
        assert metrics["guest_agent_enabled"] == 1
        assert metrics["freemem_bytes"] == 4294967296
        assert metrics["balloon_actual_bytes"] == 8589934592
        # Detail counters should overwrite inventory
        assert metrics["diskread_bytes_total"] == 107374182500

    def test_stopped_guest(self):
        """Stopped guests should have NULL runtime fields but valid status."""
        inventory = {
            "vmid": 102,
            "type": "qemu",
            "name": "offline-vm",
            "status": "stopped",
            "is_running": 0,
            "maxcpu": 2,
            "maxmem": 4294967,  # KiB
        }
        details = {}

        metrics = build_guest_metrics(inventory, details)

        assert metrics["status"] == "stopped"
        assert metrics["is_running"] == 0
        assert metrics["name"] == "offline-vm"
        # Runtime fields should be None
        assert metrics["uptime_s"] is None
        assert metrics["cpu_host_pct"] is None
        assert metrics["cpu_of_allocated_pct"] is None
        assert metrics["mem_bytes"] is None
        assert metrics["diskread_bytes_total"] is None

    def test_paused_guest(self):
        """Paused guests should record status without runtime metrics."""
        inventory = {
            "vmid": 103,
            "type": "lxc",
            "name": "paused-ct",
            "status": "paused",
            "is_running": 0,
        }

        metrics = build_guest_metrics(inventory, {})

        assert metrics["status"] == "paused"
        assert metrics["is_running"] == 0
        assert metrics["cpu_host_pct"] is None

    def test_running_lxc_with_pressure(self):
        """Running LXC with pressure metrics should populate those fields."""
        inventory = {
            "vmid": 101,
            "type": "lxc",
            "name": "test-ct",
            "status": "running",
            "is_running": 1,
            "cpu": 0.35,
            "maxcpu": 4,
            "mem": 2097152,
            "maxmem": 4194304,
            "uptime": 7200,
        }
        details = {
            "swap": 524288,
            "pressurecpusome": 0.05,
            "pressureiosome": 0.02,
            "pressurememorysome": 0.01,
        }

        metrics = build_guest_metrics(inventory, details)

        assert metrics["status"] == "running"
        assert metrics["swap_bytes"] == 524288
        assert metrics["pressurecpusome"] == 0.05
        assert metrics["pressureiosome"] == 0.02
        assert metrics["pressurememorysome"] == 0.01

    def test_guest_detail_fetch_failure(self):
        """When detail fetch fails for running guest, use inventory data."""
        inventory = {
            "vmid": 100,
            "type": "qemu",
            "name": "test-vm",
            "status": "running",
            "is_running": 1,
            "cpu": 0.5,
            "maxcpu": 4,
            "mem": 4194304,
            "maxmem": 8388608,
            "uptime": 1000,
        }

        metrics = build_guest_metrics(inventory, {})

        # Should still record running state with inventory values
        assert metrics["is_running"] == 1
        assert metrics["status"] == "running"
        assert metrics["cpu_host_pct"] == 50.0
        assert metrics["uptime_s"] == 1000

    def test_unknown_status_guest(self):
        """Guest with unknown status should be treated as not running."""
        inventory = {
            "vmid": 999,
            "type": "qemu",
            "name": "mystery-vm",
            "status": "unknown",
            "is_running": 0,
        }

        metrics = build_guest_metrics(inventory, {})

        assert metrics["status"] == "unknown"
        assert metrics["is_running"] == 0
