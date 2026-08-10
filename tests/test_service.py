"""Integration tests: collection into the in-memory store served by the API."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

import pvemonitor.collector as collector_module
from pvemonitor.api_routes.guests import guest_range, guests_list
from pvemonitor.api_routes.health import health
from pvemonitor.api_routes.host import host_latest, host_range
from pvemonitor.config import Config
from pvemonitor.deps import set_storage
from pvemonitor.storage import Storage


def _make_config(tmp_path: Path) -> Config:
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
    cfg_path = tmp_path / "monitor.yaml"
    cfg_path.write_text(yaml.safe_dump(data))
    return Config(str(cfg_path))


@pytest.fixture
def storage(tmp_path) -> Storage:
    store = Storage(_make_config(tmp_path))
    set_storage(store)
    yield store
    set_storage(None)
    store.close()


def _stub_collection(monkeypatch) -> None:
    inventory = [
        {
            "vmid": 100,
            "type": "qemu",
            "name": "vm-100",
            "status": "running",
            "is_running": 1,
            "cpu": 0.5,
            "maxcpu": 4,
            "mem": 1073741824,
            "maxmem": 2147483648,
            "uptime": 3600,
            "diskread": 1000,
            "diskwrite": 500,
            "netin": 300,
            "netout": 200,
        }
    ]

    monkeypatch.setattr(
        collector_module,
        "collect_host_metrics",
        lambda config: {
            "cpu_usage_pct": 10.0,
            "mem_total_bytes": 1000,
            "mem_used_bytes": 500,
        },
    )
    monkeypatch.setattr(
        collector_module,
        "collect_gpu_metrics",
        lambda config: {"gpu_name": "test-gpu", "gpu_busy_pct": 50.0},
    )
    monkeypatch.setattr(
        collector_module,
        "collect_guest_inventory",
        lambda config: inventory,
    )
    monkeypatch.setattr(
        collector_module,
        "collect_guest_details",
        lambda vmid, gtype, node: {
            "uptime": 3600,
            "cpus": 4,
            "maxmem": 2147483648,
            "mem": 1073741824,
            "cpu": 0.5,
        },
    )


def test_collect_once_populates_store(storage, monkeypatch):
    _stub_collection(monkeypatch)

    errors = collector_module.collect_once(storage, storage.config, print_summary=False)

    assert errors == 0
    counts = storage.table_counts()
    assert counts["samples"] == 1
    assert counts["host_metrics"] == 1
    assert counts["guest_samples"] == 1
    assert counts["guests"] == 1


def test_debug_collect_does_not_write_snapshot(tmp_path, monkeypatch):
    _stub_collection(monkeypatch)
    config = _make_config(tmp_path)

    errors = collector_module.run_collection(config)

    assert errors == 0
    assert list(config.snapshot_dir.glob("telemetry-*.sqlite3")) == []


def test_api_reads_live_store(storage, monkeypatch):
    _stub_collection(monkeypatch)
    collector_module.collect_once(storage, storage.config, print_summary=False)

    latest = asyncio.run(host_latest())
    assert latest["cpu_usage_pct"] == 10.0
    assert latest["gpu_busy_pct"] == 50.0

    guests = asyncio.run(guests_list())
    assert len(guests) == 1
    assert guests[0]["vmid"] == 100
    assert guests[0]["cpu_host_pct"] == 50.0

    rows = asyncio.run(host_range(from_="-1h", to="now", resolution="auto"))
    assert len(rows) == 1
    assert rows[0]["epoch_s"] > 0
    assert rows[0]["cpu_usage_pct"] == 10.0

    g_rows = asyncio.run(guest_range(100, from_="-1h", to="now", resolution="auto"))
    assert len(g_rows) == 1

    transitions = asyncio.run(_transitions())
    assert len(transitions) == 1
    assert transitions[0]["transition"] == "first_seen"

    health_result = asyncio.run(health())
    assert health_result["total_samples"] == 1
    assert health_result["memory_used_bytes"] > 0
    assert health_result["memory_limit_bytes"] == 256 * 1024 * 1024
    assert health_result["status"] == "ok"


def _transitions():
    from pvemonitor.api_routes.guests import guest_transitions

    return guest_transitions(100)


def test_snapshot_reload_after_restart(storage, monkeypatch, tmp_path):
    _stub_collection(monkeypatch)
    collector_module.collect_once(storage, storage.config, print_summary=False)
    path = storage.snapshot()
    assert path is not None and path.exists()
    storage.close()

    set_storage(None)
    restarted = Storage(_make_config(tmp_path))
    set_storage(restarted)
    try:
        loaded = restarted.load_latest_snapshot()
        assert loaded is not None
        assert restarted.table_counts()["samples"] == 1
        latest = asyncio.run(host_latest())
        assert latest["cpu_usage_pct"] == 10.0
    finally:
        set_storage(None)
        restarted.close()
