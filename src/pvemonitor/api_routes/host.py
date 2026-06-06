"""Host metrics API endpoints for PVEmonitor."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..deps import get_db
from ..rollups import choose_series_resolution

router = APIRouter(prefix="/api/host", tags=["host"])

VALID_HOST_FIELDS: set[str] = {
    "load1", "load5", "load15",
    "cpu_usage_pct", "cpu_user_pct", "cpu_system_pct", "cpu_iowait_pct", "cpu_idle_pct",
    "cpu_temp_c",
    "mem_total_bytes", "mem_used_bytes", "mem_free_bytes",
    "swap_total_bytes", "swap_used_bytes",
    "rootfs_total_bytes", "rootfs_used_bytes", "rootfs_free_bytes",
    "gpu_name", "gpu_busy_pct", "gpu_temp_c", "gpu_power_w", "gpu_vram_used_pct",
    "psi_cpu_some_avg10", "psi_cpu_some_avg60", "psi_cpu_some_avg300",
    "psi_cpu_full_avg10", "psi_cpu_full_avg60", "psi_cpu_full_avg300",
    "psi_io_some_avg10", "psi_io_some_avg60", "psi_io_some_avg300",
    "psi_io_full_avg10", "psi_io_full_avg60", "psi_io_full_avg300",
    "psi_mem_some_avg10", "psi_mem_some_avg60", "psi_mem_some_avg300",
    "psi_mem_full_avg10", "psi_mem_full_avg60", "psi_mem_full_avg300",
    "top_cpu_process_name", "top_cpu_process_pid", "top_cpu_process_pct",
}


def _parse_epoch(value: str) -> int:
    value = value.strip()
    if value == "now":
        return int(time.time())
    rel = re.match(r"^-(\d+)(m|h|d)$", value)
    if rel:
        amount = int(rel.group(1))
        unit = rel.group(2)
        scale = {"m": 60, "h": 3600, "d": 86400}[unit]
        return int(time.time()) - (amount * scale)

    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())



def _resolve_fields(fields: str | None) -> list[str] | None:
    if not fields:
        return None
    requested = {f.strip() for f in fields.split(",") if f.strip()}
    valid = [f for f in requested if f in VALID_HOST_FIELDS]
    return valid if valid else None



def _resolve_resolution(resolution: str, window_s: int) -> int | None:
    if resolution == "auto":
        return choose_series_resolution(window_s)
    if resolution == "raw":
        return None
    if resolution == "1m":
        return 60
    if resolution == "5m":
        return 300
    raise HTTPException(status_code=400, detail="resolution must be one of auto, raw, 1m, 5m")



def _select_host_rows(
    conn,
    from_epoch: int,
    to_epoch: int,
    selected_fields: list[str] | None,
    resolution: str = "auto",
) -> list[dict]:
    if from_epoch > to_epoch:
        raise HTTPException(status_code=400, detail="from must be <= to")

    window_s = max(0, to_epoch - from_epoch)
    chosen_resolution = _resolve_resolution(resolution, window_s)
    cols = ", ".join(f"h.{f}" for f in selected_fields) if selected_fields else "h.*"

    def raw_rows() -> list[dict]:
        rows = conn.execute(
            f"""SELECT s.ts, s.epoch_s, s.hostname, {cols},
                       NULL AS _resolution_s,
                       1 AS _sample_count
                FROM samples s
                JOIN host_metrics h ON h.sample_id = s.id
                WHERE s.epoch_s >= ? AND s.epoch_s <= ?
                ORDER BY s.epoch_s ASC""",
            (from_epoch, to_epoch),
        ).fetchall()
        return [dict(r) for r in rows]

    if chosen_resolution is None:
        return raw_rows()

    rows = conn.execute(
        f"""SELECT h.ts,
                   h.bucket_epoch_s AS epoch_s,
                   h.hostname,
                   {cols},
                   h.resolution_s AS _resolution_s,
                   h.sample_count AS _sample_count
            FROM host_rollups h
            WHERE h.resolution_s = ?
              AND h.bucket_epoch_s >= ?
              AND h.bucket_epoch_s <= ?
            ORDER BY h.bucket_epoch_s ASC""",
        (chosen_resolution, from_epoch, to_epoch),
    ).fetchall()
    if rows:
        return [dict(r) for r in rows]
    return raw_rows()


@router.get("/latest")
async def host_latest():
    """Most recent host_metrics row joined with samples.ts."""
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT s.ts, s.epoch_s, s.hostname, s.collection_ms, s.error_count,
                      h.*
               FROM samples s
               JOIN host_metrics h ON h.sample_id = s.id
               ORDER BY s.id DESC LIMIT 1"""
        ).fetchone()

        if row is None:
            raise HTTPException(status_code=404, detail="No host metrics found")

        return dict(row)
    finally:
        conn.close()


@router.get("/range")
async def host_range(
    from_: str = Query(default="-1h", alias="from"),
    to: str = Query(default="now", alias="to"),
    fields: Optional[str] = None,
    resolution: str = Query(default="auto"),
):
    """Host metrics array for a time range."""
    conn = get_db()
    try:
        from_epoch = _parse_epoch(from_)
        to_epoch = _parse_epoch(to)
        selected_fields = _resolve_fields(fields)
        return _select_host_rows(conn, from_epoch, to_epoch, selected_fields, resolution)
    finally:
        conn.close()


@router.get("/summary")
async def host_summary(window_s: int = Query(default=3600, ge=60, le=86400)):
    """Min/max/avg for key host metrics over a time window."""
    conn = get_db()
    try:
        cutoff_epoch = int(time.time()) - window_s
        metrics = [
            "gpu_temp_c", "gpu_busy_pct", "gpu_power_w", "gpu_vram_used_pct",
            "cpu_usage_pct", "cpu_temp_c",
            "load1", "load5", "load15",
            "mem_used_bytes", "mem_free_bytes", "swap_used_bytes",
            "rootfs_used_bytes", "rootfs_free_bytes",
        ]

        result: dict = {"window_s": window_s, "sample_count": 0}
        for col in metrics:
            row = conn.execute(
                f"""SELECT
                        MIN(h.{col}) as min_val,
                        MAX(h.{col}) as max_val,
                        ROUND(AVG(h.{col}), 2) as avg_val
                    FROM host_metrics h
                    JOIN samples s ON s.id = h.sample_id
                    WHERE s.epoch_s >= ?""",
                (cutoff_epoch,),
            ).fetchone()
            if row and row["avg_val"] is not None:
                result[col] = {
                    "min": row["min_val"],
                    "max": row["max_val"],
                    "avg": row["avg_val"],
                }

        count_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM samples WHERE epoch_s >= ?",
            (cutoff_epoch,),
        ).fetchone()
        result["sample_count"] = count_row["cnt"]
        return result
    finally:
        conn.close()
