"""Guest metrics API endpoints for PVEmonitor."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from ..deps import get_db
from ..rollups import choose_series_resolution

router = APIRouter(prefix="/api/guests", tags=["guests"])


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



def _select_guest_rows(
    conn,
    vmid: int,
    from_epoch: int,
    to_epoch: int,
    resolution: str = "auto",
) -> list[dict]:
    if from_epoch > to_epoch:
        raise HTTPException(status_code=400, detail="from must be <= to")

    window_s = max(0, to_epoch - from_epoch)
    chosen_resolution = _resolve_resolution(resolution, window_s)

    def raw_rows() -> list[dict]:
        rows = conn.execute(
            """SELECT s.ts, s.epoch_s,
                      gs.guest_type, gs.name, gs.status, gs.is_running,
                      gs.uptime_s, gs.cpu_host_pct, gs.cpu_of_allocated_pct,
                      gs.maxcpu,
                      gs.mem_bytes, gs.maxmem_bytes, gs.mem_pct,
                      gs.diskread_bps, gs.diskwrite_bps,
                      gs.netin_bps, gs.netout_bps,
                      NULL AS _resolution_s,
                      1 AS _sample_count
               FROM guest_samples gs
               JOIN samples s ON s.id = gs.sample_id
               WHERE gs.vmid = ?
                 AND s.epoch_s >= ?
                 AND s.epoch_s <= ?
               ORDER BY s.epoch_s ASC""",
            (vmid, from_epoch, to_epoch),
        ).fetchall()
        return [dict(r) for r in rows]

    if chosen_resolution is None:
        return raw_rows()

    rows = conn.execute(
        """SELECT gr.ts,
                  gr.bucket_epoch_s AS epoch_s,
                  gr.guest_type, gr.name, gr.status, gr.is_running,
                  gr.uptime_s, gr.cpu_host_pct, gr.cpu_of_allocated_pct,
                  gr.maxcpu,
                  gr.mem_bytes, gr.maxmem_bytes, gr.mem_pct,
                  gr.diskread_bps, gr.diskwrite_bps,
                  gr.netin_bps, gr.netout_bps,
                  gr.resolution_s AS _resolution_s,
                  gr.sample_count AS _sample_count
           FROM guest_rollups gr
           WHERE gr.vmid = ?
             AND gr.resolution_s = ?
             AND gr.bucket_epoch_s >= ?
             AND gr.bucket_epoch_s <= ?
           ORDER BY gr.bucket_epoch_s ASC""",
        (vmid, chosen_resolution, from_epoch, to_epoch),
    ).fetchall()
    if rows:
        return [dict(r) for r in rows]
    return raw_rows()


@router.get("")
async def guests_list():
    """Current guest status summary from the latest sample."""
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT gs.vmid, gs.guest_type, gs.name, gs.status, gs.is_running,
                      gs.uptime_s, gs.cpu_host_pct, gs.cpu_of_allocated_pct,
                      gs.mem_bytes, gs.mem_pct, gs.maxmem_bytes,
                      gs.diskread_bps, gs.diskwrite_bps,
                      gs.netin_bps, gs.netout_bps,
                      gs.freemem_bytes, gs.guest_agent_enabled
               FROM guest_samples gs
               WHERE gs.sample_id = (SELECT MAX(id) FROM samples)
               ORDER BY gs.guest_type, gs.vmid"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/{vmid}/range")
async def guest_range(
    vmid: int,
    from_: str = Query(default="-1h", alias="from"),
    to: str = Query(default="now", alias="to"),
    resolution: str = Query(default="auto"),
):
    """Time-series for a specific guest (CPU, mem, disk, net)."""
    conn = get_db()
    try:
        from_epoch = _parse_epoch(from_)
        to_epoch = _parse_epoch(to)
        rows = _select_guest_rows(conn, vmid, from_epoch, to_epoch, resolution)
        if not rows:
            raise HTTPException(status_code=404, detail=f"No data for VMID {vmid} in that range")
        return rows
    finally:
        conn.close()


@router.get("/{vmid}/transitions")
async def guest_transitions(vmid: int):
    """Up/down state transitions for a specific guest."""
    conn = get_db()
    try:
        rows = conn.execute(
            """WITH ordered AS (
                   SELECT s.ts, s.epoch_s,
                          gs.vmid, gs.guest_type, gs.name,
                          gs.status, gs.is_running, gs.uptime_s,
                          LAG(gs.is_running) OVER (
                              PARTITION BY gs.vmid, gs.guest_type ORDER BY s.epoch_s
                          ) AS prev_running
                   FROM guest_samples gs
                   JOIN samples s ON s.id = gs.sample_id
                   WHERE gs.vmid = ?
               )
               SELECT ts, vmid, guest_type, name, status, uptime_s,
                      CASE
                          WHEN prev_running IS NULL THEN 'first_seen'
                          WHEN prev_running = 0 AND is_running = 1 THEN 'came_up'
                          WHEN prev_running = 1 AND is_running = 0 THEN 'went_down'
                          ELSE 'no_change'
                      END AS transition
               FROM ordered
               WHERE prev_running IS NULL OR prev_running != is_running
               ORDER BY ts""",
            (vmid,),
        ).fetchall()

        if not rows:
            raise HTTPException(status_code=404, detail=f"No data for VMID {vmid}")

        return [dict(r) for r in rows]
    finally:
        conn.close()
