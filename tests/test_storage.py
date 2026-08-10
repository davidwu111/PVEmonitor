"""Tests for the in-memory telemetry store."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pvemonitor.config import Config
from pvemonitor.db import (
    insert_guest_sample,
    insert_host_metrics,
    insert_sample,
    upsert_guest,
)
from pvemonitor.storage import Storage, latest_snapshot


def _make_config(tmp_path: Path, **storage_overrides) -> Config:
    data = {
        "monitor": {"sample_interval_s": 1},
        "paths": {
            "db": str(tmp_path / "legacy.sqlite3"),
            "log": str(tmp_path / "collector.log"),
            "lock": str(tmp_path / "collector.lock"),
        },
        "storage": {
            "memory_limit_mb": 256,
            "snapshot_enabled": True,
            "snapshot_interval_minutes": 60,
            "snapshot_keep": 2,
            "snapshot_dir": str(tmp_path / "snapshots"),
            "import_legacy_db": False,
        },
    }
    data["storage"].update(storage_overrides)
    cfg_path = tmp_path / "monitor.yaml"
    cfg_path.write_text(yaml.safe_dump(data))
    return Config(str(cfg_path))


def _insert_sample(conn, epoch_s: int, vmid: int = 100) -> int:
    ts = f"2026-06-06T00:00:{epoch_s % 60:02d}Z"
    sample_id = insert_sample(conn, ts, epoch_s, "pve", "0.1.0", 100)
    insert_host_metrics(
        conn,
        sample_id,
        {"cpu_usage_pct": 10.0, "mem_used_bytes": 1000},
    )
    upsert_guest(conn, vmid, "qemu", ts, "vm")
    insert_guest_sample(
        conn,
        sample_id,
        vmid,
        "qemu",
        {
            "name": "vm",
            "status": "running",
            "is_running": 1,
            "cpu_host_pct": 5.0,
            "mem_bytes": 1024,
            "maxmem_bytes": 4096,
        },
    )
    conn.commit()
    return sample_id


def test_schema_created(tmp_path):
    storage = Storage(_make_config(tmp_path))
    try:
        tables = {
            row[0]
            for row in storage.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        expected = {
            "samples",
            "host_metrics",
            "guest_samples",
            "guests",
            "collector_errors",
            "host_rollups",
            "guest_rollups",
        }
        assert expected.issubset(tables)
    finally:
        storage.close()


def test_snapshot_roundtrip(tmp_path):
    config = _make_config(tmp_path)
    storage = Storage(config)
    try:
        _insert_sample(storage.connection, 1000)
        _insert_sample(storage.connection, 1010)
        path = storage.snapshot()
        assert path is not None and path.exists()
    finally:
        storage.close()

    reloaded = Storage(_make_config(tmp_path))
    try:
        loaded = reloaded.load_latest_snapshot()
        assert loaded is not None
        counts = reloaded.table_counts()
        assert counts["samples"] == 2
        assert counts["host_metrics"] == 2
        assert counts["guest_samples"] == 2
        assert counts["guests"] == 1
    finally:
        reloaded.close()


def test_snapshot_keeps_only_configured_count(tmp_path):
    storage = Storage(_make_config(tmp_path, snapshot_keep=2))
    try:
        _insert_sample(storage.connection, 1000)
        for i in range(3):
            storage.snapshot(storage.config.snapshot_dir / f"telemetry-{i}.sqlite3")
        files = list((storage.config.snapshot_dir).glob("telemetry-*.sqlite3"))
        assert len(files) == 2
    finally:
        storage.close()


def test_memory_cap_evicts_oldest_and_keeps_newest(tmp_path):
    config = _make_config(tmp_path)
    storage = Storage(config, memory_limit_bytes=12_000)
    try:
        with storage.lock() as conn:
            for epoch in range(1, 31):
                _insert_sample(conn, 1000 + epoch)

        result = storage.enforce_memory_cap()
        assert result is not None
        assert result["samples_deleted"] > 0

        counts = storage.table_counts()
        assert storage.estimated_bytes(counts) <= config.memory_limit_bytes * 0.9
        newest = storage.connection.execute(
            "SELECT MAX(epoch_s) AS e FROM samples"
        ).fetchone()["e"]
        oldest = storage.connection.execute(
            "SELECT MIN(epoch_s) AS e FROM samples"
        ).fetchone()["e"]
        assert newest == 1030
        assert oldest > 1001
    finally:
        storage.close()


def test_memory_cap_keeps_newest_even_with_tiny_limit(tmp_path):
    storage = Storage(_make_config(tmp_path), memory_limit_bytes=1)
    try:
        with storage.lock() as conn:
            for epoch in range(1, 6):
                _insert_sample(conn, 1000 + epoch)
        storage.enforce_memory_cap()
        assert storage.table_counts()["samples"] == 1
        newest = storage.connection.execute(
            "SELECT MAX(epoch_s) AS e FROM samples"
        ).fetchone()["e"]
        assert newest == 1005
    finally:
        storage.close()


def test_read_handle_materializes_rows(tmp_path):
    storage = Storage(_make_config(tmp_path))
    try:
        _insert_sample(storage.connection, 1000)
        handle = storage.read_handle()
        row = handle.execute(
            "SELECT epoch_s, hostname FROM samples"
        ).fetchone()
        assert row is not None and row["epoch_s"] == 1000
        assert row["hostname"] == "pve"
        assert handle.execute("SELECT 1").fetchone() is not None
        handle.close()  # must be a no-op
        row = handle.execute(
            "SELECT COUNT(*) AS c FROM samples"
        ).fetchone()
        assert row["c"] == 1
    finally:
        storage.close()


def test_stats_report_memory_and_snapshot_fields(tmp_path):
    storage = Storage(_make_config(tmp_path, memory_limit_mb=4))
    try:
        _insert_sample(storage.connection, 1000)
        stats = storage.stats()
        assert stats["memory_used_bytes"] > 0
        assert stats["memory_limit_bytes"] == 4 * 1024 * 1024
        assert stats["table_counts"]["samples"] == 1
        assert stats["last_snapshot_path"] is None
        assert stats["last_snapshot_age_s"] is None
    finally:
        storage.close()


def test_latest_snapshot_helper(tmp_path):
    assert latest_snapshot(tmp_path / "missing") is None
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()
    (snap_dir / "telemetry-1.sqlite3").touch()
    (snap_dir / "telemetry-2.sqlite3").touch()
    assert latest_snapshot(snap_dir).name == "telemetry-2.sqlite3"


def test_invalid_storage_config_rejected(tmp_path):
    with pytest.raises(ValueError):
        _make_config(tmp_path, memory_limit_mb=0)
    with pytest.raises(ValueError):
        _make_config(tmp_path, snapshot_interval_minutes=0)
    with pytest.raises(ValueError):
        _make_config(tmp_path, snapshot_keep=0)
