"""Rollup aggregation and retention helpers for PVEmonitor."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from .config import Config

logger = logging.getLogger(__name__)

ROLLUP_RESOLUTIONS: tuple[int, ...] = (60, 300)

HOST_AVG_REAL_FIELDS: tuple[str, ...] = (
    "load1",
    "load5",
    "load15",
    "cpu_usage_pct",
    "cpu_user_pct",
    "cpu_system_pct",
    "cpu_iowait_pct",
    "cpu_idle_pct",
    "cpu_temp_c",
    "gpu_busy_pct",
    "gpu_temp_c",
    "gpu_power_w",
    "gpu_vram_used_pct",
    "psi_cpu_some_avg10",
    "psi_cpu_some_avg60",
    "psi_cpu_some_avg300",
    "psi_cpu_full_avg10",
    "psi_cpu_full_avg60",
    "psi_cpu_full_avg300",
    "psi_io_some_avg10",
    "psi_io_some_avg60",
    "psi_io_some_avg300",
    "psi_io_full_avg10",
    "psi_io_full_avg60",
    "psi_io_full_avg300",
    "psi_mem_some_avg10",
    "psi_mem_some_avg60",
    "psi_mem_some_avg300",
    "psi_mem_full_avg10",
    "psi_mem_full_avg60",
    "psi_mem_full_avg300",
    "top_cpu_process_pct",
)

HOST_AVG_INT_FIELDS: tuple[str, ...] = (
    "mem_total_bytes",
    "mem_used_bytes",
    "mem_free_bytes",
    "swap_total_bytes",
    "swap_used_bytes",
    "rootfs_total_bytes",
    "rootfs_used_bytes",
    "rootfs_free_bytes",
)

HOST_LATEST_FIELDS: tuple[str, ...] = (
    "gpu_name",
    "top_cpu_process_name",
    "top_cpu_process_pid",
)

GUEST_AVG_REAL_FIELDS: tuple[str, ...] = (
    "cpu_host_pct",
    "cpu_of_allocated_pct",
    "mem_pct",
    "diskread_bps",
    "diskwrite_bps",
    "netin_bps",
    "netout_bps",
)

GUEST_AVG_INT_FIELDS: tuple[str, ...] = (
    "mem_bytes",
    "maxmem_bytes",
)

GUEST_LATEST_FIELDS: tuple[str, ...] = (
    "name",
    "status",
    "is_running",
    "uptime_s",
    "maxcpu",
)


def bucket_start_epoch(epoch_s: int, resolution_s: int) -> int:
    return epoch_s - (epoch_s % resolution_s)


def choose_series_resolution(window_s: int) -> int | None:
    """Choose an automatic rollup resolution for a requested window.

    Returns None for raw samples.
    """
    if window_s <= 24 * 3600:
        return None
    if window_s <= 7 * 24 * 3600:
        return 60
    return 300


def resolution_label(resolution_s: int | None) -> str:
    if resolution_s is None:
        return "raw"
    if resolution_s % 3600 == 0:
        return f"{resolution_s // 3600}h"
    if resolution_s % 60 == 0:
        return f"{resolution_s // 60}m"
    return f"{resolution_s}s"


def _avg(rows: list[sqlite3.Row], field: str, *, integer: bool = False) -> float | int | None:
    values = [row[field] for row in rows if row[field] is not None]
    if not values:
        return None
    avg = sum(values) / len(values)
    if integer:
        return int(round(avg))
    return round(avg, 4)


def _latest_non_null(rows: list[sqlite3.Row], field: str) -> Any:
    for row in reversed(rows):
        value = row[field]
        if value is not None:
            return value
    return None


def _upsert(conn: sqlite3.Connection, table: str, payload: dict[str, Any], conflict_cols: tuple[str, ...]) -> None:
    columns = list(payload.keys())
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(
        f"{col} = excluded.{col}" for col in columns if col not in conflict_cols
    )
    conn.execute(
        f"""INSERT INTO {table} ({', '.join(columns)})
            VALUES ({placeholders})
            ON CONFLICT({', '.join(conflict_cols)}) DO UPDATE SET {updates}""",
        [payload[col] for col in columns],
    )


def refresh_host_rollup(conn: sqlite3.Connection, resolution_s: int, bucket_epoch_s: int) -> None:
    rows = conn.execute(
        """SELECT s.ts, s.epoch_s, s.hostname, h.*
           FROM samples s
           JOIN host_metrics h ON h.sample_id = s.id
           WHERE s.epoch_s >= ? AND s.epoch_s < ?
           ORDER BY s.epoch_s ASC""",
        (bucket_epoch_s, bucket_epoch_s + resolution_s),
    ).fetchall()

    if not rows:
        conn.execute(
            "DELETE FROM host_rollups WHERE resolution_s = ? AND bucket_epoch_s = ?",
            (resolution_s, bucket_epoch_s),
        )
        return

    payload: dict[str, Any] = {
        "resolution_s": resolution_s,
        "bucket_epoch_s": bucket_epoch_s,
        "ts": rows[-1]["ts"],
        "hostname": _latest_non_null(rows, "hostname"),
        "sample_count": len(rows),
    }
    for field in HOST_AVG_REAL_FIELDS:
        payload[field] = _avg(rows, field)
    for field in HOST_AVG_INT_FIELDS:
        payload[field] = _avg(rows, field, integer=True)
    for field in HOST_LATEST_FIELDS:
        payload[field] = _latest_non_null(rows, field)

    _upsert(conn, "host_rollups", payload, ("resolution_s", "bucket_epoch_s"))


def refresh_guest_rollup(
    conn: sqlite3.Connection,
    resolution_s: int,
    bucket_epoch_s: int,
    vmid: int,
    guest_type: str,
) -> None:
    rows = conn.execute(
        """SELECT s.ts, s.epoch_s, gs.*
           FROM guest_samples gs
           JOIN samples s ON s.id = gs.sample_id
           WHERE gs.vmid = ?
             AND gs.guest_type = ?
             AND s.epoch_s >= ?
             AND s.epoch_s < ?
           ORDER BY s.epoch_s ASC""",
        (vmid, guest_type, bucket_epoch_s, bucket_epoch_s + resolution_s),
    ).fetchall()

    if not rows:
        conn.execute(
            """DELETE FROM guest_rollups
               WHERE resolution_s = ? AND bucket_epoch_s = ? AND vmid = ? AND guest_type = ?""",
            (resolution_s, bucket_epoch_s, vmid, guest_type),
        )
        return

    payload: dict[str, Any] = {
        "resolution_s": resolution_s,
        "bucket_epoch_s": bucket_epoch_s,
        "ts": rows[-1]["ts"],
        "vmid": vmid,
        "guest_type": guest_type,
        "sample_count": len(rows),
    }
    for field in GUEST_AVG_REAL_FIELDS:
        payload[field] = _avg(rows, field)
    for field in GUEST_AVG_INT_FIELDS:
        payload[field] = _avg(rows, field, integer=True)
    for field in GUEST_LATEST_FIELDS:
        payload[field] = _latest_non_null(rows, field)

    _upsert(
        conn,
        "guest_rollups",
        payload,
        ("resolution_s", "bucket_epoch_s", "vmid", "guest_type"),
    )


def refresh_rollups_for_sample(
    conn: sqlite3.Connection,
    epoch_s: int,
    guest_keys: list[tuple[int, str]],
) -> None:
    for resolution_s in ROLLUP_RESOLUTIONS:
        bucket_epoch_s = bucket_start_epoch(epoch_s, resolution_s)
        refresh_host_rollup(conn, resolution_s, bucket_epoch_s)
        for vmid, guest_type in guest_keys:
            refresh_guest_rollup(conn, resolution_s, bucket_epoch_s, vmid, guest_type)


def backfill_all_rollups(conn: sqlite3.Connection) -> None:
    logger.info("Backfilling rollups from existing raw samples")
    for resolution_s in ROLLUP_RESOLUTIONS:
        buckets = conn.execute(
            "SELECT DISTINCT ((epoch_s / ?) * ?) AS bucket_epoch_s FROM samples ORDER BY bucket_epoch_s",
            (resolution_s, resolution_s),
        ).fetchall()
        for row in buckets:
            refresh_host_rollup(conn, resolution_s, row["bucket_epoch_s"])

        guest_buckets = conn.execute(
            """SELECT DISTINCT gs.vmid, gs.guest_type, ((s.epoch_s / ?) * ?) AS bucket_epoch_s
               FROM guest_samples gs
               JOIN samples s ON s.id = gs.sample_id
               ORDER BY gs.vmid, gs.guest_type, bucket_epoch_s""",
            (resolution_s, resolution_s),
        ).fetchall()
        for row in guest_buckets:
            refresh_guest_rollup(
                conn,
                resolution_s,
                row["bucket_epoch_s"],
                row["vmid"],
                row["guest_type"],
            )


def maybe_backfill_rollups(conn: sqlite3.Connection) -> bool:
    raw_samples = conn.execute("SELECT COUNT(*) AS cnt FROM samples").fetchone()["cnt"]
    if raw_samples == 0:
        return False

    host_rollups = conn.execute("SELECT COUNT(*) AS cnt FROM host_rollups").fetchone()["cnt"]
    guest_samples = conn.execute("SELECT COUNT(*) AS cnt FROM guest_samples").fetchone()["cnt"]
    guest_rollups = conn.execute("SELECT COUNT(*) AS cnt FROM guest_rollups").fetchone()["cnt"]

    needs_host_backfill = host_rollups == 0
    needs_guest_backfill = guest_samples > 0 and guest_rollups == 0
    if not needs_host_backfill and not needs_guest_backfill:
        return False

    backfill_all_rollups(conn)
    return True


def prune_old_raw_samples(conn: sqlite3.Connection, cutoff_epoch_s: int) -> int:
    cur = conn.execute("DELETE FROM samples WHERE epoch_s < ?", (cutoff_epoch_s,))
    return cur.rowcount if cur.rowcount is not None else 0


def prune_old_rollups(conn: sqlite3.Connection, config: Config, now_epoch_s: int) -> dict[str, int]:
    cutoffs = {
        60: now_epoch_s - (config.rollup_1m_retention_days * 86400),
        300: now_epoch_s - (config.rollup_5m_retention_days * 86400),
    }
    stats = {
        "host_rollups_deleted": 0,
        "guest_rollups_deleted": 0,
    }
    for resolution_s, cutoff_epoch_s in cutoffs.items():
        cur = conn.execute(
            "DELETE FROM host_rollups WHERE resolution_s = ? AND bucket_epoch_s < ?",
            (resolution_s, cutoff_epoch_s),
        )
        stats["host_rollups_deleted"] += cur.rowcount if cur.rowcount is not None else 0
        cur = conn.execute(
            "DELETE FROM guest_rollups WHERE resolution_s = ? AND bucket_epoch_s < ?",
            (resolution_s, cutoff_epoch_s),
        )
        stats["guest_rollups_deleted"] += cur.rowcount if cur.rowcount is not None else 0
    return stats


def maybe_run_retention(conn: sqlite3.Connection, config: Config, sample_id: int, epoch_s: int) -> dict[str, int] | None:
    if config.maintenance_interval_samples <= 0:
        return None
    if sample_id % config.maintenance_interval_samples != 0:
        return None

    raw_cutoff_epoch_s = epoch_s - (config.raw_retention_days * 86400)
    deleted_raw = prune_old_raw_samples(conn, raw_cutoff_epoch_s)
    rollup_stats = prune_old_rollups(conn, config, epoch_s)
    stats = {
        "raw_samples_deleted": deleted_raw,
        **rollup_stats,
    }
    logger.info("Retention maintenance complete: %s", stats)
    return stats
