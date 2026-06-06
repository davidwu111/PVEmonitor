"""Host metrics API endpoints for PVEmonitor."""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..api import get_db

router = APIRouter(prefix="/api/host", tags=["host"])

# Valid host_metrics column names (whitelist for fields parameter)
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


def _parse_time(value: str) -> str:
    """Parse a time parameter: ISO 8601 or relative like '-2h', '-30m'.

    Returns an ISO 8601 string for SQLite datetime comparison.
    """
    value = value.strip()
    if value.startswith("-"):
        # Relative time
        return f"datetime('now', '{value}')"
    # Absolute ISO 8601
    return f"'{value}'"


def _resolve_fields(fields: str | None) -> list[str] | None:
    """Parse a comma-separated fields list, whitelisting valid columns."""
    if not fields:
        return None
    requested = {f.strip() for f in fields.split(",") if f.strip()}
    valid = [f for f in requested if f in VALID_HOST_FIELDS]
    return valid if valid else None


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
):
    """Host metrics array for a time range.

    Args:
        from_: Start time (ISO 8601 or relative like '-2h').
        to: End time (ISO 8601 or relative, defaults to 'now').
        fields: Comma-separated column names to limit payload.
    """
    conn = get_db()
    try:
        from_sql = _parse_time(from_)
        to_sql = _parse_time(to)

        selected_fields = _resolve_fields(fields)
        cols = ", ".join(f"h.{f}" for f in selected_fields) if selected_fields else "h.*"

        rows = conn.execute(
            f"""SELECT s.ts, s.epoch_s, s.hostname, {cols}
                FROM samples s
                JOIN host_metrics h ON h.sample_id = s.id
                WHERE s.ts >= {from_sql} AND s.ts <= {to_sql}
                ORDER BY s.ts ASC"""
        ).fetchall()

        return [dict(r) for r in rows]

    finally:
        conn.close()


@router.get("/summary")
async def host_summary(window_s: int = Query(default=3600, ge=60, le=86400)):
    """Min/max/avg for key host metrics over a time window.

    Args:
        window_s: Window size in seconds (default 3600 = 1 hour).
    """
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

        # Sample count
        count_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM samples WHERE epoch_s >= ?",
            (cutoff_epoch,),
        ).fetchone()
        result["sample_count"] = count_row["cnt"]

        return result

    finally:
        conn.close()
