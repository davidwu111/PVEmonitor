"""Shared test fixtures for PVEmonitor tests."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import pytest

# Path to test fixtures
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def pvesh_resources() -> list[dict]:
    """Load sample pvesh /cluster/resources output."""
    with open(FIXTURES_DIR / "pvesh_resources.json") as f:
        return json.load(f)


@pytest.fixture
def pvesh_status() -> dict:
    """Load sample pvesh /status output."""
    with open(FIXTURES_DIR / "pvesh_status.json") as f:
        return json.load(f)


@pytest.fixture
def rocm_smi_output() -> str:
    """Load sample rocm-smi text output."""
    with open(FIXTURES_DIR / "rocm-smi_output.txt") as f:
        return f.read()


@pytest.fixture
def sensors_output() -> str:
    """Load sample sensors text output."""
    with open(FIXTURES_DIR / "sensors_output.txt") as f:
        return f.read()


@pytest.fixture
def temp_db() -> sqlite3.Connection:
    """Create a temporary in-memory SQLite database with the full schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # Apply schema migrations
    schema_dir = Path(__file__).parent.parent / "sql" / "schema"
    for sql_file in sorted(schema_dir.glob("*.sql")):
        sql = sql_file.read_text()
        conn.executescript(sql)

    conn.commit()
    return conn


@pytest.fixture
def seeded_db(temp_db: sqlite3.Connection) -> sqlite3.Connection:
    """Database with a seed sample and some guest data for rate calculation."""
    conn = temp_db

    # Insert a sample at epoch 1000
    conn.execute(
        "INSERT INTO samples (id, ts, epoch_s, hostname, collector_version) VALUES (1, '2026-06-06T00:00:10Z', 1000, 'pve', '0.1.0')"
    )

    # Insert host_metrics (minimal)
    conn.execute(
        """INSERT INTO host_metrics (sample_id, cpu_usage_pct, cpu_temp_c, load1)
           VALUES (1, 25.0, 45.0, 1.0)"""
    )

    # Insert guests
    conn.execute(
        "INSERT INTO guests (vmid, guest_type, first_seen_ts, last_seen_ts, current_name) "
        "VALUES (100, 'qemu', '2026-06-06T00:00:00Z', '2026-06-06T00:00:10Z', 'test-vm')"
    )
    conn.execute(
        "INSERT INTO guests (vmid, guest_type, first_seen_ts, last_seen_ts, current_name) "
        "VALUES (101, 'lxc', '2026-06-06T00:00:00Z', '2026-06-06T00:00:10Z', 'test-ct')"
    )

    # Insert guest_samples for sample 1 (running guests)
    conn.execute(
        """INSERT INTO guest_samples (
               sample_id, vmid, guest_type, name, status, is_running,
               uptime_s, maxcpu, cpu_host_pct, cpu_of_allocated_pct,
               mem_bytes, maxmem_bytes, mem_pct,
               diskread_bytes_total, diskwrite_bytes_total,
               netin_bytes_total, netout_bytes_total
           ) VALUES (
               1, 100, 'qemu', 'test-vm', 'running', 1,
               3600, 8, 75.0, 9.375,
               8589934592, 17179869184, 50.0,
               100000000, 50000000,
               200000000, 100000000
           )"""
    )
    conn.execute(
        """INSERT INTO guest_samples (
               sample_id, vmid, guest_type, name, status, is_running,
               uptime_s, maxcpu, cpu_host_pct, cpu_of_allocated_pct,
               mem_bytes, maxmem_bytes, mem_pct,
               diskread_bytes_total, diskwrite_bytes_total,
               netin_bytes_total, netout_bytes_total
           ) VALUES (
               1, 101, 'lxc', 'test-ct', 'running', 1,
               7200, 4, 12.5, 3.125,
               2147483648, 4294967296, 50.0,
               10000000, 5000000,
               20000000, 10000000
           )"""
    )

    # Insert a stopped guest for sample 1
    conn.execute(
        """INSERT INTO guest_samples (
               sample_id, vmid, guest_type, name, status, is_running
           ) VALUES (
               1, 102, 'qemu', 'offline-vm', 'stopped', 0
           )"""
    )

    conn.commit()
    return conn
