"""Main collection orchestrator for PVEmonitor.

One full collection run: collect host → collect guests → compute rates
→ insert samples/host_metrics/guest_samples into the in-memory store → commit.

`serve` runs collections on a fixed interval from a background thread;
`collect` is a one-shot debug invocation that collects into a throwaway
in-memory store and prints a summary (it does not feed the live service).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from .config import Config, get_config
from .db import (
    get_previous_guest_sample,
    insert_error,
    insert_guest_sample,
    insert_host_metrics,
    insert_sample,
    upsert_guest,
)
from .gpu import collect_gpu_metrics
from .guests import (
    build_guest_metrics,
    collect_guest_details,
    collect_guest_inventory,
)
from .host import collect_host_metrics
from .rates import compute_guest_rates
from .rollups import maybe_backfill_rollups, maybe_run_retention, refresh_rollups_for_sample
from .storage import Storage
from .util import setup_logging

logger = logging.getLogger(__name__)


def collect_once(
    storage: Storage,
    config: Config,
    print_summary: bool = True,
    write_snapshot: bool = True,
) -> int:
    """Run one full collection cycle into the in-memory store.

    External collection calls run without holding the store lock; database
    work (rates, inserts, rollups, retention) is done under the store lock in
    a single transaction. The memory cap is enforced and a snapshot may be
    written after each committed sample.

    Returns:
        Number of errors encountered during collection.
    """
    start_time = time.time()
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    epoch_s = int(now.timestamp())
    error_count = 0

    # 1. Collect host metrics
    host_metrics: dict = {}
    if config.host_enabled:
        try:
            host_metrics = collect_host_metrics(config)
        except Exception as exc:
            logger.error("Host metrics collection failed: %s", exc)
            print(f"  host: FAILED — {exc}")
            error_count += 1

    # 2. Collect GPU metrics
    if config.gpu_enabled:
        try:
            gpu_metrics = collect_gpu_metrics(config)
            host_metrics.update(gpu_metrics)
        except Exception as exc:
            logger.error("GPU metrics collection failed: %s", exc)
            print(f"  gpu: FAILED — {exc}")
            error_count += 1

    # 3. Collect guest inventory
    guests = []
    if config.guests_enabled:
        try:
            guests = collect_guest_inventory(config)
        except Exception as exc:
            logger.error("Guest inventory collection failed: %s", exc)
            print(f"  guests: FAILED — {exc}")
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
                details = collect_guest_details(vmid, gtype, item.get("node"))
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

    # 5-10. Database work under the store lock
    with storage.lock() as conn:
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

        # 9. Maintain rollups and retention before commit
        guest_keys = [(item["vmid"], item["type"]) for item, _ in guest_metrics_list]
        try:
            backfilled = maybe_backfill_rollups(conn)
            refresh_rollups_for_sample(conn, epoch_s, guest_keys)
            retention_stats = maybe_run_retention(conn, config, sample_id, epoch_s)
            if backfilled:
                logger.info("Rollup backfill completed")
            if retention_stats:
                logger.info("Retention maintenance summary: %s", retention_stats)
        except Exception as exc:
            logger.error("Rollup/retention maintenance failed: %s", exc)
            insert_error(conn, sample_id, "maintenance", str(exc))
            error_count += 1

        # 10. Commit transaction
        conn.commit()

    # 11. Enforce memory cap and write a snapshot if due (outside the transaction)
    storage.enforce_memory_cap()
    if write_snapshot:
        storage.maybe_snapshot()

    running_count = sum(1 for _, m in guest_metrics_list if m.get("is_running"))
    total_guests = len(guest_metrics_list)
    elapsed_ms = int((time.time() - start_time) * 1000)

    summary = f"sample {sample_id} — {total_guests} guests ({running_count} running), {len(host_metrics)} host metrics, {elapsed_ms}ms"
    if error_count > 0:
        summary += f", {error_count} errors"

    logger.info("Collection complete: %s", summary)
    if print_summary:
        print(summary)

    return error_count


def run_collection(
    config: Config | None = None,
    force: bool = False,
) -> int:
    """Run one debug collection into a throwaway in-memory store.

    The legacy ``force`` flag is accepted for CLI compatibility and is a
    no-op: collection no longer uses a lock file.
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

    storage = Storage(config)
    try:
        # Debug run: never write a snapshot, so a throwaway collection cannot
        # shadow the live service's snapshot history.
        return collect_once(
            storage,
            config,
            print_summary=True,
            write_snapshot=False,
        )
    finally:
        storage.close()


def run_collection_loop(
    storage: Storage,
    config: Config,
    stop_event: threading.Event,
) -> None:
    """Background collection loop used by the API service.

    Runs one collection immediately, then every ``sample_interval_s`` seconds
    with drift-free scheduling. The loop is interruptible via ``stop_event``.
    """
    interval = max(1, config.sample_interval_s)
    next_run = time.monotonic()
    while not stop_event.is_set():
        try:
            collect_once(storage, config, print_summary=False)
        except Exception as exc:
            logger.exception("Collection run failed: %s", exc)

        next_run += interval
        delay = next_run - time.monotonic()
        if delay <= 0:
            # Collection took longer than the interval; reschedule from now.
            next_run = time.monotonic() + interval
            delay = interval
        if stop_event.wait(delay):
            return
