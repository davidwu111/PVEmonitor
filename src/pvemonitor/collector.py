"""Main collection orchestrator for PVEmonitor.

One full collection run: lock → collect host → collect guests → compute rates
→ insert samples/host_metrics/guest_samples → commit.

Handles partial failures gracefully — if one guest fails, host and other guests
are still committed.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone

from . import __version__
from .config import Config, get_config
from .db import (
    ensure_schema,
    get_connection,
    get_previous_guest_sample,
    insert_error,
    insert_guest_sample,
    insert_host_metrics,
    insert_sample,
    maybe_checkpoint,
    upsert_guest,
)
from .gpu import collect_gpu_metrics
from .guests import (
    build_guest_metrics,
    collect_guest_details,
    collect_guest_inventory,
)
from .host import collect_host_metrics
from .locking import acquire_lock, release_lock
from .rates import compute_guest_rates
from .util import setup_logging

logger = logging.getLogger(__name__)


def run_collection(
    config: Config | None = None,
    force: bool = False,
) -> int:
    """Run one full collection cycle.

    Args:
        config: Config instance (created if None).
        force: If True, force lock acquisition (skip stale checks).

    Returns:
        Number of errors encountered during collection.
    """
    if config is None:
        config = get_config()

    # Setup logging
    setup_logging(
        str(config.log_path),
        level=config.log_level,
        max_bytes=config.log_max_bytes,
        backup_count=config.log_backup_count,
    )

    start_time = time.time()

    # Acquire non-overlap lock
    if not acquire_lock(config.lock_path, config.stale_lock_timeout_s, force=force):
        logger.info("Could not acquire lock — another collector is running. Exiting.")
        return 0

    error_count = 0

    try:
        conn = get_connection(str(config.db_path))

        try:
            # Ensure schema is up to date
            ensure_schema(conn, config)

            # Timestamp
            now = datetime.now(timezone.utc)
            ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            epoch_s = int(now.timestamp())

            # 1. Collect host metrics
            host_metrics: dict = {}
            if config.host_enabled:
                try:
                    host_metrics = collect_host_metrics(config)
                except Exception as exc:
                    logger.error("Host metrics collection failed: %s", exc)
                    error_count += 1

            # 2. Collect GPU metrics
            if config.gpu_enabled:
                try:
                    gpu_metrics = collect_gpu_metrics(config)
                    host_metrics.update(gpu_metrics)
                except Exception as exc:
                    logger.error("GPU metrics collection failed: %s", exc)
                    error_count += 1

            # 3. Collect guest inventory
            guests = []
            if config.guests_enabled:
                try:
                    guests = collect_guest_inventory(config)
                except Exception as exc:
                    logger.error("Guest inventory collection failed: %s", exc)
                    error_count += 1

            # 4. For each guest, collect details and build metrics
            guest_metrics_list: list[tuple[dict, dict]] = []
            # (inventory_item, built_metrics)

            for item in guests:
                vmid = item["vmid"]
                gtype = item["type"]

                try:
                    if item["is_running"]:
                        # Running guest: fetch detailed status
                        details = collect_guest_details(vmid, gtype)
                        if not details and item["status"] == "running":
                            # pvesh may have failed; use inventory data, mark unknown
                            logger.warning(
                                "Detail fetch failed for running %s %d; using inventory data",
                                gtype, vmid,
                            )
                    else:
                        # Non-running guest: no detail fetch needed
                        details = {}

                    metrics = build_guest_metrics(item, details)
                    guest_metrics_list.append((item, metrics))

                except Exception as exc:
                    logger.error("Failed to collect %s %d: %s", gtype, vmid, exc)
                    error_count += 1
                    # Still record the guest with unknown status
                    metrics = {
                        "name": item.get("name"),
                        "status": "unknown",
                        "is_running": 0,
                        "uptime_s": None,
                        "maxcpu": None,
                        "cpu_host_pct": None,
                        "cpu_of_allocated_pct": None,
                        "mem_bytes": None,
                        "maxmem_bytes": None,
                        "mem_pct": None,
                        "swap_bytes": None,
                        "diskread_bytes_total": None,
                        "diskwrite_bytes_total": None,
                        "netin_bytes_total": None,
                        "netout_bytes_total": None,
                        "diskread_bps": None,
                        "diskwrite_bps": None,
                        "netin_bps": None,
                        "netout_bps": None,
                        "pressurecpusome": None,
                        "pressurecpufull": None,
                        "pressureiosome": None,
                        "pressureiofull": None,
                        "pressurememorysome": None,
                        "pressurememoryfull": None,
                        "freemem_bytes": None,
                        "balloon_actual_bytes": None,
                        "guest_agent_enabled": None,
                    }
                    guest_metrics_list.append((item, metrics))

            # 5. Compute rates for running guests with previous data
            for item, metrics in guest_metrics_list:
                vmid = item["vmid"]
                gtype = item["type"]
                if metrics["is_running"]:
                    try:
                        prev = get_previous_guest_sample(conn, vmid, gtype)
                        rates = compute_guest_rates(
                            metrics, prev, epoch_s, config.max_rate_interval_s,
                        )
                        metrics.update(rates)
                    except Exception as exc:
                        logger.warning("Rate calculation failed for %s %d: %s", gtype, vmid, exc)

            # 6. Insert sample
            collection_ms = int((time.time() - start_time) * 1000)
            try:
                sample_id = insert_sample(
                    conn, ts, epoch_s, config.hostname,
                    config.collector_version, collection_ms, error_count,
                )
            except Exception as exc:
                logger.error("Failed to insert sample row: %s", exc)
                conn.rollback()
                return error_count + 1

            # 7. Insert host_metrics
            if host_metrics:
                try:
                    insert_host_metrics(conn, sample_id, host_metrics)
                except Exception as exc:
                    logger.error("Failed to insert host_metrics: %s", exc)
                    insert_error(conn, sample_id, "host", str(exc))
                    error_count += 1

            # 8. Upsert guests and insert guest_samples
            for item, metrics in guest_metrics_list:
                vmid = item["vmid"]
                gtype = item["type"]

                try:
                    upsert_guest(conn, vmid, gtype, ts, metrics.get("name"))
                except Exception as exc:
                    logger.warning("Failed to upsert guest %s %d: %s", gtype, vmid, exc)

                try:
                    insert_guest_sample(conn, sample_id, vmid, gtype, metrics)
                except Exception as exc:
                    logger.error("Failed to insert guest_sample for %s %d: %s", gtype, vmid, exc)
                    insert_error(conn, sample_id, f"guest:{vmid}", str(exc), vmid, gtype)
                    error_count += 1

            # 9. Commit transaction
            conn.commit()
            logger.info(
                "Collection complete: sample %d, %d host metrics, %d guests, %d errors in %dms",
                sample_id, len(host_metrics), len(guest_metrics_list),
                error_count, collection_ms,
            )

            # 10. Periodic WAL maintenance
            maybe_checkpoint(conn)

        finally:
            conn.close()

    finally:
        # Always release the lock
        release_lock(config.lock_path)

    return error_count
