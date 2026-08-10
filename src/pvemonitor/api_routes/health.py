"""Health check endpoint for PVEmonitor API."""

from __future__ import annotations

import time

from fastapi import APIRouter

from ..deps import get_db, get_storage

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health():
    """Health check: store readability, latest sample age, memory usage.

    Returns:
        {"status": "ok", "latest_sample_ts": "...", "latest_sample_age_s": N,
         "total_samples": N, "memory_used_bytes": N, "memory_limit_bytes": N,
         "last_snapshot_ts": "...", "last_snapshot_age_s": N}
    """
    store = get_storage()
    conn = get_db()
    try:
        # Latest sample
        row = conn.execute(
            "SELECT ts, epoch_s FROM samples ORDER BY id DESC LIMIT 1"
        ).fetchone()

        total_row = conn.execute("SELECT COUNT(*) as cnt FROM samples").fetchone()
        total = total_row["cnt"]
        stats = store.stats()

        if row is None:
            return {
                "status": "degraded",
                "reason": "No samples in store",
                "hostname": None,
                "latest_sample_ts": None,
                "latest_sample_age_s": None,
                "total_samples": 0,
                "memory_used_bytes": stats["memory_used_bytes"],
                "memory_limit_bytes": stats["memory_limit_bytes"],
                "last_snapshot_ts": stats["last_snapshot_ts"],
                "last_snapshot_age_s": stats["last_snapshot_age_s"],
            }

        ts = row["ts"]
        latest_epoch = row["epoch_s"]
        age_s = int(time.time()) - latest_epoch

        host_row = conn.execute(
            "SELECT hostname FROM samples ORDER BY id DESC LIMIT 1"
        ).fetchone()
        hostname = host_row["hostname"] if host_row is not None else None

        # Determine health status
        status = "ok"
        reason = None
        if age_s > 300:  # 5 minutes
            status = "degraded"
            reason = f"Latest sample is {age_s}s old"
        elif stats["last_snapshot_ts"] is None:
            status = "degraded"
            reason = "No snapshot written yet"

        result = {
            "status": status,
            "hostname": hostname,
            "latest_sample_ts": ts,
            "latest_sample_age_s": age_s,
            "total_samples": total,
            "memory_used_bytes": stats["memory_used_bytes"],
            "memory_limit_bytes": stats["memory_limit_bytes"],
            "last_snapshot_ts": stats["last_snapshot_ts"],
            "last_snapshot_age_s": stats["last_snapshot_age_s"],
        }
        if reason:
            result["reason"] = reason
        return result
    finally:
        conn.close()
