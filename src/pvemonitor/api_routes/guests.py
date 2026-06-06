"""Guest metrics API endpoints for PVEmonitor."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..deps import get_db

router = APIRouter(prefix="/api/guests", tags=["guests"])


def _parse_time(value: str) -> str:
    """Parse a time parameter into an SQL expression."""
    value = value.strip()
    if value == "now":
        return "datetime('now')"
    if value.startswith("-"):
        return f"datetime('now', '{value}')"
    return f"'{value}'"


@router.get("")
async def guests_list():
    """Current guest status summary from the latest sample.

    Returns the most recent status + key metrics for each known guest.
    """
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
):
    """Time-series for a specific guest (CPU, mem, disk, net).

    Args:
        vmid: Guest VMID.
        from_: Start time (ISO 8601 or relative).
        to: End time (ISO 8601 or relative).
    """
    conn = get_db()
    try:
        from_sql = _parse_time(from_)
        to_sql = _parse_time(to)

        rows = conn.execute(
            f"""SELECT s.ts, s.epoch_s,
                       gs.guest_type, gs.name, gs.status, gs.is_running,
                       gs.uptime_s, gs.cpu_host_pct, gs.cpu_of_allocated_pct,
                       gs.maxcpu,
                       gs.mem_bytes, gs.maxmem_bytes, gs.mem_pct,
                       gs.diskread_bps, gs.diskwrite_bps,
                       gs.netin_bps, gs.netout_bps
                FROM guest_samples gs
                JOIN samples s ON s.id = gs.sample_id
                WHERE gs.vmid = ?
                  AND s.ts >= {from_sql}
                  AND s.ts <= {to_sql}
                ORDER BY s.ts ASC""",
            (vmid,),
        ).fetchall()

        if not rows:
            raise HTTPException(status_code=404, detail=f"No data for VMID {vmid} in that range")

        return [dict(r) for r in rows]

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
