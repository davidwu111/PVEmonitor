"""Host metrics collection for PVEmonitor.

Collects CPU, load, memory, swap, rootfs, PSI pressure, and top process.
Reads from /proc/*, /sys/*, sensors, and top.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from .config import Config
from .util import (
    SubprocessError,
    parse_float,
    parse_int,
    parse_psi_file,
    read_proc_file,
    run_cmd,
)

logger = logging.getLogger(__name__)


def collect_host_metrics(config: Config) -> dict[str, Any]:
    """Collect all host-level metrics.

    Returns a dict keyed by host_metrics column names.
    Fields that fail to collect are set to None.
    """
    metrics: dict[str, Any] = {}

    if not config.host_enabled:
        return metrics

    # Load average
    metrics.update(_collect_load())

    # CPU from top
    metrics.update(_collect_cpu())

    # CPU temperature
    metrics["cpu_temp_c"] = _collect_cpu_temp()

    # Memory and swap
    metrics.update(_collect_memory())

    # Root filesystem
    metrics.update(_collect_rootfs())

    # PSI pressure
    if config.psi_enabled:
        metrics.update(_collect_psi())

    # Top process
    if config.top_process_enabled:
        metrics.update(_collect_top_process())

    return metrics


def _collect_load() -> dict[str, float | None]:
    """Parse /proc/loadavg for load1, load5, load15."""
    result: dict[str, float | None] = {
        "load1": None,
        "load5": None,
        "load15": None,
    }
    content = read_proc_file("/proc/loadavg")
    if content:
        parts = content.split()
        if len(parts) >= 3:
            result["load1"] = parse_float(parts[0])
            result["load5"] = parse_float(parts[1])
            result["load15"] = parse_float(parts[2])
    return result


def _collect_cpu() -> dict[str, float | None]:
    """Collect CPU usage breakdown using top -bn1.

    Returns cpu_usage_pct, cpu_user_pct, cpu_system_pct, cpu_iowait_pct, cpu_idle_pct.
    """
    result: dict[str, float | None] = {
        "cpu_usage_pct": None,
        "cpu_user_pct": None,
        "cpu_system_pct": None,
        "cpu_iowait_pct": None,
        "cpu_idle_pct": None,
    }

    try:
        stdout = run_cmd(["top", "-bn1"], timeout=5.0)

        # Parse the CPU summary line: %Cpu(s):  us, sy, ni, id, wa, hi, si, st
        for line in stdout.splitlines():
            if line.startswith("%Cpu"):
                # Extract comma-separated values
                match = re.search(r"%Cpu.*?:\s*(.*)", line)
                if match:
                    parts = match.group(1).split(",")
                    pct_map = {}
                    for part in parts:
                        part = part.strip()
                        key_match = re.match(r"([\d.]+)\s+(\w+)", part)
                        if key_match:
                            pct_map[key_match.group(2)] = parse_float(key_match.group(1))

                    result["cpu_user_pct"] = pct_map.get("us")
                    result["cpu_system_pct"] = pct_map.get("sy")
                    result["cpu_iowait_pct"] = pct_map.get("wa")
                    result["cpu_idle_pct"] = pct_map.get("id")

                    # cpu_usage_pct = 100 - idle (handles all states)
                    if result["cpu_idle_pct"] is not None:
                        result["cpu_usage_pct"] = round(100.0 - result["cpu_idle_pct"], 2)
                break

    except SubprocessError as exc:
        logger.warning("top command failed: %s", exc)

    return result


def _collect_cpu_temp() -> float | None:
    """Collect CPU temperature using the `sensors` command.

    Looks for k10temp Tctl, or the first CPU-related temp.
    Falls back to /sys/class/hwmon if sensors is unavailable.
    """
    # Try sensors command first
    try:
        stdout = run_cmd(["sensors"], timeout=2.0)
        # Look for k10temp Tctl or Tdie
        for line in stdout.splitlines():
            if "Tctl" in line or "Tdie" in line:
                match = re.search(r"\+?([\d.]+)°C", line)
                if match:
                    return parse_float(match.group(1))
        # Fallback: look for any "temp1" or "CPU" temperature
        for line in stdout.splitlines():
            if "temp1" in line or "CPU" in line:
                match = re.search(r"\+?([\d.]+)°C", line)
                if match:
                    return parse_float(match.group(1))
    except SubprocessError:
        logger.debug("sensors command failed; trying sysfs")

    # Sysfs fallback
    import glob

    hwmon_dirs = sorted(glob.glob("/sys/class/hwmon/hwmon*"))
    for hwmon in hwmon_dirs:
        try:
            name_path = f"{hwmon}/name"
            name = read_proc_file(name_path)
            if name and ("k10temp" in name or "coretemp" in name or "cpu" in name.lower()):
                for temp_file in sorted(glob.glob(f"{hwmon}/temp*_input")):
                    val_str = read_proc_file(temp_file)
                    if val_str:
                        temp = parse_int(val_str)
                        if temp:
                            return temp / 1000.0
        except Exception:
            continue

    return None


def _collect_memory() -> dict[str, int | None]:
    """Parse /proc/meminfo for memory and swap stats."""
    result: dict[str, int | None] = {
        "mem_total_bytes": None,
        "mem_used_bytes": None,
        "mem_free_bytes": None,
        "swap_total_bytes": None,
        "swap_used_bytes": None,
    }

    content = read_proc_file("/proc/meminfo")
    if not content:
        return result

    meminfo: dict[str, int] = {}
    for line in content.splitlines():
        parts = line.split(":")
        if len(parts) < 2:
            continue
        key = parts[0].strip()
        val_str = parts[1].strip().split()[0]  # strip "kB" suffix
        val = parse_int(val_str)
        if val is not None:
            meminfo[key] = val * 1024  # convert kB to bytes

    result["mem_total_bytes"] = meminfo.get("MemTotal")
    raw_mem_free = meminfo.get("MemFree")
    available = meminfo.get("MemAvailable")
    if result["mem_total_bytes"] is not None:
        if available is not None and available <= result["mem_total_bytes"]:
            # Match Proxmox's user-facing memory accounting: used + available = total.
            result["mem_used_bytes"] = result["mem_total_bytes"] - available
            result["mem_free_bytes"] = available
        elif raw_mem_free is not None:
            # fallback: rough estimate including buffers/cached
            used = result["mem_total_bytes"] - raw_mem_free
            buffers = meminfo.get("Buffers", 0)
            cached = meminfo.get("Cached", 0)
            sreclaimable = meminfo.get("SReclaimable", 0)
            result["mem_used_bytes"] = used - buffers - cached - sreclaimable
            if result["mem_used_bytes"] is not None:
                result["mem_free_bytes"] = max(result["mem_total_bytes"] - result["mem_used_bytes"], 0)
            else:
                result["mem_free_bytes"] = raw_mem_free

    result["swap_total_bytes"] = meminfo.get("SwapTotal")
    swap_free = meminfo.get("SwapFree")
    if result["swap_total_bytes"] is not None and swap_free is not None:
        result["swap_used_bytes"] = result["swap_total_bytes"] - swap_free

    return result


def _collect_rootfs() -> dict[str, int | None]:
    """Collect root filesystem usage via statvfs."""
    result: dict[str, int | None] = {
        "rootfs_total_bytes": None,
        "rootfs_used_bytes": None,
        "rootfs_free_bytes": None,
    }
    try:
        st = os.statvfs("/")
        block_size = st.f_frsize
        result["rootfs_total_bytes"] = st.f_blocks * block_size
        result["rootfs_free_bytes"] = st.f_bavail * block_size
        result["rootfs_used_bytes"] = (st.f_blocks - st.f_bfree) * block_size
    except OSError as exc:
        logger.warning("statvfs(/) failed: %s", exc)
    return result


def _collect_psi() -> dict[str, float | None]:
    """Collect Pressure Stall Information from /proc/pressure/{cpu,io,memory}.

    Returns flat keys like psi_cpu_some_avg10, psi_io_full_avg60, etc.
    """
    result: dict[str, float | None] = {}
    psi_files = {
        "cpu": "/proc/pressure/cpu",
        "io": "/proc/pressure/io",
        "mem": "/proc/pressure/memory",
    }

    for source, path in psi_files.items():
        psi_data = parse_psi_file(path)
        for key, val in psi_data.items():
            # key is like "some_avg10", transform to "psi_cpu_some_avg10"
            col = f"psi_{source}_{key}"
            result[col] = val

    return result


def _collect_top_process() -> dict[str, Any]:
    """Identify the top CPU-consuming process (excluding idle/system).

    Uses ps instead of top parsing for reliability.
    """
    result: dict[str, Any] = {
        "top_cpu_process_name": None,
        "top_cpu_process_pid": None,
        "top_cpu_process_pct": None,
    }

    try:
        stdout = run_cmd(
            ["ps", "-eo", "pid,comm,%cpu", "--sort=-%cpu", "--no-headers"],
            timeout=3.0,
        )
        lines = stdout.splitlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            pid_str, comm, cpu_str = parts
            pid = parse_int(pid_str)
            cpu = parse_float(cpu_str)
            if pid is None or cpu is None:
                continue
            # Skip PID 0, 1, 2 (kernel/init) and our own ps process
            if pid <= 2:
                continue
            result["top_cpu_process_pid"] = pid
            result["top_cpu_process_name"] = comm[:255]  # truncate to reasonable length
            result["top_cpu_process_pct"] = cpu
            break
    except SubprocessError as exc:
        logger.warning("ps command failed for top process: %s", exc)

    return result
