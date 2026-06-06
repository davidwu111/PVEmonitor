"""Guest (VM/LXC) metrics collection for PVEmonitor.

Uses Proxmox pvesh command to query guest inventory and per-guest details.
Handles running, stopped, paused, and unknown guest states.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .config import Config
from .util import (
    SubprocessError,
    parse_float,
    parse_int,
    run_cmd,
    safe_get,
)

logger = logging.getLogger(__name__)

# pvesh command timeouts (seconds)
PVESH_STATUS_TIMEOUT = 5.0
PVESH_RESOURCES_TIMEOUT = 10.0
PVESH_GUEST_DETAIL_TIMEOUT = 5.0


def collect_guest_inventory(config: Config) -> list[dict[str, Any]]:
    """Fetch the full guest inventory from /cluster/resources.

    Returns a list of dicts, each representing a VM or LXC with:
        vmid, type, name, status, is_running, and basic resource counters.
    """
    try:
        raw = run_cmd(
            ["pvesh", "get", "/cluster/resources", "--type", "vm", "--output-format", "json"],
            timeout=PVESH_RESOURCES_TIMEOUT,
        )
        if not raw:
            return []
        data = json.loads(raw)
    except (SubprocessError, json.JSONDecodeError) as exc:
        logger.warning("Failed to fetch guest inventory: %s", exc)
        return []

    if not isinstance(data, list):
        return []

    guests = []
    for item in data:
        if not isinstance(item, dict):
            continue
        vmid = item.get("vmid")
        gtype = item.get("type")
        if vmid is None or not gtype:
            continue
        if gtype not in ("qemu", "lxc"):
            continue

        status = item.get("status", "unknown")
        guests.append({
            "vmid": vmid,
            "type": gtype,
            "node": item.get("node"),
            "name": item.get("name", str(vmid)),
            "status": status,
            "is_running": 1 if status == "running" else 0,
            # Basic counters from inventory (overwritten by detail fetch for running guests)
            "cpu": item.get("cpu"),
            "maxcpu": item.get("maxcpu"),
            "mem": item.get("mem"),
            "maxmem": item.get("maxmem"),
            "disk": item.get("disk"),
            "netout": item.get("netout"),
            "netin": item.get("netin"),
            "uptime": item.get("uptime"),
            # Additional fields
            "template": item.get("template", 0),
        })

    return guests


def collect_guest_details(
    vmid: int,
    guest_type: str,
    node: str | None = None,
) -> dict[str, Any]:
    """Fetch detailed status for a specific guest.

    Calls pvesh get /nodes/<node>/<qemu|lxc>/<vmid>/status/current.

    Returns a dict of guest detail fields, or an empty dict on failure.
    """
    resolved_node = node or "pve"
    path = f"/nodes/{resolved_node}/{guest_type}/{vmid}/status/current"

    try:
        raw = run_cmd(
            ["pvesh", "get", path, "--output-format", "json"],
            timeout=PVESH_GUEST_DETAIL_TIMEOUT,
        )
        if not raw:
            return {}
        return json.loads(raw)
    except (SubprocessError, json.JSONDecodeError) as exc:
        logger.warning(
            "Failed to fetch details for %s %d on node %s: %s",
            guest_type,
            vmid,
            resolved_node,
            exc,
        )
        return {}


def build_guest_metrics(
    inventory_item: dict[str, Any],
    details: dict[str, Any],
) -> dict[str, Any]:
    """Build a guest_samples row dict from inventory + details.

    Args:
        inventory_item: From collect_guest_inventory (basic status + counters).
        details: From collect_guest_details (rich runtime data for running guests),
                 or empty dict for non-running guests.

    Returns a dict with keys matching guest_samples columns.
    """
    is_running = inventory_item["is_running"]
    gtype = inventory_item["type"]

    metrics: dict[str, Any] = {
        "name": inventory_item.get("name"),
        "status": inventory_item["status"],
        "is_running": is_running,
        # Runtime fields default to None
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

    # Populate from inventory first (always available)
    metrics["maxcpu"] = parse_int(inventory_item.get("maxcpu"))
    metrics["maxmem_bytes"] = parse_int(inventory_item.get("maxmem"))

    mem_val = parse_int(inventory_item.get("mem"))
    if mem_val is not None:
        metrics["mem_bytes"] = mem_val

    # Memory percentage
    if metrics["mem_bytes"] and metrics["maxmem_bytes"] and metrics["maxmem_bytes"] > 0:
        metrics["mem_pct"] = round((metrics["mem_bytes"] / metrics["maxmem_bytes"]) * 100, 2)

    # CPU: cpu is a float representing fraction of host CPU
    cpu_val = parse_float(inventory_item.get("cpu"))
    if cpu_val is not None:
        metrics["cpu_host_pct"] = round(cpu_val * 100, 2)
        if metrics["maxcpu"] and metrics["maxcpu"] > 0:
            metrics["cpu_of_allocated_pct"] = round((cpu_val / metrics["maxcpu"]) * 100, 2)

    # Uptime from inventory
    metrics["uptime_s"] = parse_int(inventory_item.get("uptime"))

    # Cumulative counters from inventory
    metrics["diskread_bytes_total"] = parse_int(inventory_item.get("diskread"))
    metrics["diskwrite_bytes_total"] = parse_int(inventory_item.get("diskwrite"))
    metrics["netin_bytes_total"] = parse_int(inventory_item.get("netin"))
    metrics["netout_bytes_total"] = parse_int(inventory_item.get("netout"))

    # If guest is not running, return now with NULL runtime fields
    if not is_running:
        return metrics

    # --- Running guest: enrich with detail data ---
    if not details:
        # Detail fetch failed — keep inventory values, mark status as known but no detail
        return metrics

    # Uptime from detail (more accurate)
    detail_uptime = parse_int(details.get("uptime"))
    if detail_uptime is not None:
        metrics["uptime_s"] = detail_uptime

    # Max CPU from detail
    detail_maxcpu = parse_int(details.get("cpus"))
    if detail_maxcpu is not None:
        metrics["maxcpu"] = detail_maxcpu

    # Memory from detail
    detail_maxmem = parse_int(details.get("maxmem"))
    if detail_maxmem is not None:
        metrics["maxmem_bytes"] = detail_maxmem
    detail_mem = parse_int(details.get("mem"))
    if detail_mem is not None:
        metrics["mem_bytes"] = detail_mem

    # Recompute memory percentage with detail values
    if metrics["mem_bytes"] and metrics["maxmem_bytes"] and metrics["maxmem_bytes"] > 0:
        metrics["mem_pct"] = round((metrics["mem_bytes"] / metrics["maxmem_bytes"]) * 100, 2)

    # CPU from detail
    detail_cpu = parse_float(details.get("cpu"))
    inventory_cpu = parse_float(inventory_item.get("cpu"))
    effective_cpu = detail_cpu
    if inventory_cpu is not None and (effective_cpu is None or (effective_cpu == 0 and inventory_cpu > 0)):
        effective_cpu = inventory_cpu
    if effective_cpu is not None:
        metrics["cpu_host_pct"] = round(effective_cpu * 100, 2)
        if metrics["maxcpu"] and metrics["maxcpu"] > 0:
            metrics["cpu_of_allocated_pct"] = round((effective_cpu / metrics["maxcpu"]) * 100, 2)

    # Cumulative counters from detail (prefer over inventory)
    detail_diskread = parse_int(details.get("diskread"))
    if detail_diskread is not None:
        metrics["diskread_bytes_total"] = detail_diskread
    detail_diskwrite = parse_int(details.get("diskwrite"))
    if detail_diskwrite is not None:
        metrics["diskwrite_bytes_total"] = detail_diskwrite
    detail_netin = parse_int(details.get("netin"))
    if detail_netin is not None:
        metrics["netin_bytes_total"] = detail_netin
    detail_netout = parse_int(details.get("netout"))
    if detail_netout is not None:
        metrics["netout_bytes_total"] = detail_netout

    # LXC-specific: swap and pressure
    if gtype == "lxc":
        swap_val = parse_int(details.get("swap"))
        if swap_val is not None:
            metrics["swap_bytes"] = swap_val
        metrics["pressurecpusome"] = parse_float(safe_get(details, "pressurecpusome"))
        metrics["pressurecpufull"] = parse_float(safe_get(details, "pressurecpufull"))
        metrics["pressureiosome"] = parse_float(safe_get(details, "pressureiosome"))
        metrics["pressureiofull"] = parse_float(safe_get(details, "pressureiofull"))
        metrics["pressurememorysome"] = parse_float(safe_get(details, "pressurememorysome"))
        metrics["pressurememoryfull"] = parse_float(safe_get(details, "pressurememoryfull"))

    # QEMU-specific: guest agent + balloon
    if gtype == "qemu":
        agent_val = details.get("agent")
        if agent_val is not None:
            metrics["guest_agent_enabled"] = 1 if agent_val else 0
        freemem = parse_int(details.get("freemem"))
        if freemem is not None:
            metrics["freemem_bytes"] = freemem
        balloon = parse_int(details.get("ballooninfo", {}).get("actual") if isinstance(details.get("ballooninfo"), dict) else details.get("balloon"))
        if balloon is not None:
            metrics["balloon_actual_bytes"] = balloon

    return metrics
