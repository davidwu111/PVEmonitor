from __future__ import annotations

from pvemonitor.api_routes.guests import _select_guest_rows
from pvemonitor.api_routes.host import _select_host_rows
from pvemonitor.db import insert_guest_sample, insert_host_metrics, insert_sample, upsert_guest
from pvemonitor.rollups import backfill_all_rollups, choose_series_resolution



def _seed_series(conn) -> None:
    samples = [
        ("2026-06-01T00:00:00Z", 1000, 10.0, 5.0),
        ("2026-06-01T00:00:10Z", 1010, 20.0, 10.0),
        ("2026-06-01T00:01:10Z", 1070, 30.0, 15.0),
    ]
    for ts, epoch_s, host_cpu, guest_cpu in samples:
        sample_id = insert_sample(conn, ts, epoch_s, "pve", "0.1.0", 100)
        insert_host_metrics(conn, sample_id, {"cpu_usage_pct": host_cpu, "mem_used_bytes": 1000})
        upsert_guest(conn, 200, "qemu", ts, "vm-200")
        insert_guest_sample(
            conn,
            sample_id,
            200,
            "qemu",
            {
                "name": "vm-200",
                "status": "running",
                "is_running": 1,
                "uptime_s": epoch_s,
                "maxcpu": 4,
                "cpu_host_pct": guest_cpu,
                "cpu_of_allocated_pct": guest_cpu / 4,
                "mem_bytes": 4096,
                "maxmem_bytes": 8192,
                "mem_pct": 50.0,
                "diskread_bps": 100.0,
                "diskwrite_bps": 50.0,
                "netin_bps": 25.0,
                "netout_bps": 10.0,
            },
        )
    conn.commit()
    backfill_all_rollups(conn)
    conn.commit()



def test_choose_series_resolution() -> None:
    assert choose_series_resolution(24 * 3600) is None
    assert choose_series_resolution((2 * 24 * 3600)) == 60
    assert choose_series_resolution((8 * 24 * 3600)) == 300



def test_select_host_rows_uses_rollups_for_longer_windows(temp_db):
    _seed_series(temp_db)

    rows_2d = _select_host_rows(temp_db, 0, 2 * 24 * 3600, None, "auto")
    assert rows_2d[0]["_resolution_s"] == 60

    rows_8d = _select_host_rows(temp_db, 0, 8 * 24 * 3600, None, "auto")
    assert rows_8d[0]["_resolution_s"] == 300

    rows_raw = _select_host_rows(temp_db, 900, 1100, None, "auto")
    assert rows_raw[0]["_resolution_s"] is None



def test_select_guest_rows_uses_rollups_for_longer_windows(temp_db):
    _seed_series(temp_db)

    rows_2d = _select_guest_rows(temp_db, 200, 0, 2 * 24 * 3600, "auto")
    assert rows_2d[0]["_resolution_s"] == 60

    rows_8d = _select_guest_rows(temp_db, 200, 0, 8 * 24 * 3600, "auto")
    assert rows_8d[0]["_resolution_s"] == 300

    rows_raw = _select_guest_rows(temp_db, 200, 900, 1100, "auto")
    assert rows_raw[0]["_resolution_s"] is None
