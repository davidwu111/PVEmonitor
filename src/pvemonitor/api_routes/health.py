"""Health check endpoint for PVEmonitor API."""

from __future__ import annotations

import os
import time

from fastapi import APIRouter, HTTPException

from ..deps import get_db

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health():
    """Health check: DB readability, latest sample age, total samples.

    Returns:
        {"status": "ok", "latest_sample_ts": "...", "latest_sample_age_s": N,
         "total_samples": N, "db_size_bytes": N}
    """
    conn = get_db()
    try:
        # Check latest sample
        row = conn.execute(
            "SELECT ts, epoch_s FROM samples ORDER BY id DESC LIMIT 1"
        ).fetchone()

        if row is None:
            return {
                "status": "degraded",
                "reason": "No samples in database",
                "hostname": None,
                "latest_sample_ts": None,
                "latest_sample_age_s": None,
                "total_samples": 0,
            }

        ts = row["ts"]
        latest_epoch = row["epoch_s"]
        age_s = int(time.time()) - latest_epoch

        # Total samples + latest hostname
        total_row = conn.execute("SELECT COUNT(*) as cnt FROM samples").fetchone()
        total = total_row["cnt"]
        host_row = conn.execute(
            "SELECT hostname FROM samples ORDER BY id DESC LIMIT 1"
        ).fetchone()
        hostname = host_row["hostname"] if host_row is not None else None

        # DB size
        db_path = conn.execute("PRAGMA database_list").fetchone()["file"]
        try:
            db_size = os.path.getsize(db_path)
        except OSError:
            db_size = 0

        # Determine health status
        if age_s > 300:  # 5 minutes
            status = "degraded"
            reason = f"Latest sample is {age_s}s old"
        else:
            status = "ok"
            reason = None

        result = {
            "status": status,
            "hostname": hostname,
            "latest_sample_ts": ts,
            "latest_sample_age_s": age_s,
            "total_samples": total,
            "db_size_bytes": db_size,
        }
        if reason:
            result["reason"] = reason

        return result

    finally:
        conn.close()
