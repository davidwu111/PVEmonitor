from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from pvemonitor.config import Config
from pvemonitor.db import insert_guest_sample, insert_host_metrics, insert_sample, upsert_guest
from pvemonitor.rollups import backfill_all_rollups, maybe_backfill_rollups, maybe_run_retention, prune_old_raw_samples



def _seed_rollup_source(conn) -> None:
    samples = [
        ("2026-06-06T00:16:40Z", 1000, 20.0, 10.0, 2147483648),
        ("2026-06-06T00:16:50Z", 1010, 40.0, 20.0, 3221225472),
        ("2026-06-06T00:17:45Z", 1065, 80.0, 40.0, 4294967296),
    ]
    for ts, epoch_s, host_cpu, guest_cpu, guest_mem in samples:
        sample_id = insert_sample(conn, ts, epoch_s, "pve", "0.1.0", 100)
        insert_host_metrics(
            conn,
            sample_id,
            {
                "cpu_usage_pct": host_cpu,
                "cpu_temp_c": 50.0,
                "mem_total_bytes": 17179869184,
                "mem_used_bytes": 8589934592,
                "mem_free_bytes": 8589934592,
                "gpu_busy_pct": host_cpu / 2,
            },
        )
        upsert_guest(conn, 100, "qemu", ts, "vm-100")
        insert_guest_sample(
            conn,
            sample_id,
            100,
            "qemu",
            {
                "name": "vm-100",
                "status": "running",
                "is_running": 1,
                "uptime_s": epoch_s,
                "maxcpu": 4,
                "cpu_host_pct": guest_cpu,
                "cpu_of_allocated_pct": guest_cpu / 4,
                "mem_bytes": guest_mem,
                "maxmem_bytes": 8589934592,
                "mem_pct": round((guest_mem / 8589934592) * 100, 2),
                "diskread_bps": 1000.0,
                "diskwrite_bps": 500.0,
                "netin_bps": 300.0,
                "netout_bps": 200.0,
            },
        )
    conn.commit()



def test_backfill_all_rollups_aggregates_host_and_guest(temp_db):
    _seed_rollup_source(temp_db)

    backfill_all_rollups(temp_db)
    temp_db.commit()

    host_1m = temp_db.execute(
        "SELECT sample_count, cpu_usage_pct FROM host_rollups WHERE resolution_s = 60 AND bucket_epoch_s = 960"
    ).fetchone()
    assert host_1m["sample_count"] == 2
    assert host_1m["cpu_usage_pct"] == 30.0

    host_5m = temp_db.execute(
        "SELECT sample_count, cpu_usage_pct FROM host_rollups WHERE resolution_s = 300 AND bucket_epoch_s = 900"
    ).fetchone()
    assert host_5m["sample_count"] == 3
    assert host_5m["cpu_usage_pct"] == 46.6667

    guest_1m = temp_db.execute(
        """SELECT sample_count, cpu_host_pct, mem_bytes
           FROM guest_rollups
           WHERE resolution_s = 60 AND bucket_epoch_s = 960 AND vmid = 100 AND guest_type = 'qemu'"""
    ).fetchone()
    assert guest_1m["sample_count"] == 2
    assert guest_1m["cpu_host_pct"] == 15.0
    assert guest_1m["mem_bytes"] == 2684354560



def test_pruning_raw_samples_keeps_rollups_available(temp_db):
    _seed_rollup_source(temp_db)
    backfill_all_rollups(temp_db)
    temp_db.commit()

    deleted = prune_old_raw_samples(temp_db, 1050)
    temp_db.commit()

    assert deleted == 2
    assert temp_db.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 1
    assert temp_db.execute("SELECT COUNT(*) FROM host_rollups").fetchone()[0] == 3
    assert temp_db.execute("SELECT COUNT(*) FROM guest_rollups").fetchone()[0] == 3



def test_maybe_backfill_rollups_does_not_repeat_when_no_guest_samples(temp_db):
    sample_id = insert_sample(temp_db, "2026-06-06T00:16:40Z", 1000, "pve", "0.1.0", 100)
    insert_host_metrics(temp_db, sample_id, {"cpu_usage_pct": 20.0})
    temp_db.commit()

    assert maybe_backfill_rollups(temp_db) is True
    temp_db.commit()
    assert temp_db.execute("SELECT COUNT(*) FROM host_rollups").fetchone()[0] == 2

    assert maybe_backfill_rollups(temp_db) is False



def test_maybe_run_retention_prunes_old_rollups_on_interval(temp_db):
    _seed_rollup_source(temp_db)
    backfill_all_rollups(temp_db)
    temp_db.commit()

    config = SimpleNamespace(
        maintenance_interval_samples=360,
        raw_retention_days=99999,
        rollup_1m_retention_days=0,
        rollup_5m_retention_days=0,
    )

    stats = maybe_run_retention(temp_db, cast(Config, config), sample_id=360, epoch_s=1065)
    temp_db.commit()

    assert stats is not None
    assert stats["raw_samples_deleted"] == 0
    assert stats["host_rollups_deleted"] == 3
    assert stats["guest_rollups_deleted"] == 3
