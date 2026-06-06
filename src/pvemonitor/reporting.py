"""Report generation for PVEmonitor.

Provides human-readable output for the CLI report subcommands.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .config import Config, get_config
from .db import get_readonly_connection


def _connect_readonly(config: Config | None = None) -> sqlite3.Connection:
    if config is None:
        config = get_config()
    return get_readonly_connection(str(config.db_path))


def report_latest(config: Config | None = None) -> str:
    """Generate a latest-status report (host + guests).

    Returns a formatted multi-line string.
    """
    config = config or get_config()
    conn = _connect_readonly(config)
    lines: list[str] = []

    try:
        # Latest host
        row = conn.execute(
            """SELECT s.ts, s.hostname, s.collection_ms, s.error_count,
                      h.*
               FROM samples s
               JOIN host_metrics h ON h.sample_id = s.id
               ORDER BY s.id DESC LIMIT 1"""
        ).fetchone()

        if row is None:
            return "No samples found in database."

        d = dict(row)
        lines.append("=== Host Latest ===")
        lines.append(f"Timestamp:     {d['ts']}")
        lines.append(f"Hostname:      {d['hostname']}")
        lines.append(f"Load:          {d['load1']} / {d['load5']} / {d['load15']}")
        lines.append(f"CPU Usage:     {d['cpu_usage_pct']}% (usr={d['cpu_user_pct']} sys={d['cpu_system_pct']} io={d['cpu_iowait_pct']} idle={d['cpu_idle_pct']})")
        lines.append(f"CPU Temp:      {d['cpu_temp_c']}°C")
        lines.append(f"Memory:        {_fmt_bytes(d['mem_used_bytes'])} / {_fmt_bytes(d['mem_total_bytes'])} (free: {_fmt_bytes(d['mem_free_bytes'])})")
        lines.append(f"Swap:          {_fmt_bytes(d['swap_used_bytes'])} / {_fmt_bytes(d['swap_total_bytes'])}")
        lines.append(f"Rootfs:        {_fmt_bytes(d['rootfs_used_bytes'])} / {_fmt_bytes(d['rootfs_total_bytes'])} (free: {_fmt_bytes(d['rootfs_free_bytes'])})")

        if d.get("gpu_name"):
            lines.append(f"GPU:           {d['gpu_name']} — {d['gpu_busy_pct']}% busy, {d['gpu_temp_c']}°C, {d['gpu_power_w']}W, VRAM {d['gpu_vram_used_pct']}%")

        if d.get("top_cpu_process_name"):
            lines.append(f"Top Process:   {d['top_cpu_process_name']} (PID {d['top_cpu_process_pid']}, {d['top_cpu_process_pct']}% CPU)")

        # Latest guests
        lines.append("\n=== Guest Status ===")
        guests = conn.execute(
            """SELECT s.ts, gs.vmid, gs.guest_type, gs.name, gs.status,
                      gs.cpu_host_pct, gs.cpu_of_allocated_pct,
                      gs.mem_bytes, gs.mem_pct, gs.uptime_s,
                      gs.diskread_bps, gs.diskwrite_bps,
                      gs.netin_bps, gs.netout_bps
               FROM guest_samples gs
               JOIN samples s ON s.id = gs.sample_id
               WHERE gs.sample_id = (SELECT MAX(id) FROM samples)
               ORDER BY gs.guest_type, gs.vmid"""
        ).fetchall()

        for g in guests:
            gd = dict(g)
            status_icon = "✓" if gd["status"] == "running" else "✗"
            cpu_str = f"{gd['cpu_host_pct']}%" if gd["cpu_host_pct"] is not None else "-"
            mem_str = f"{gd['mem_pct']}%" if gd['mem_pct'] is not None else "-"
            lines.append(
                f"  [{gd['guest_type']:4s}] {gd['vmid']:>4d} {gd['name'] or '':20s} "
                f"{status_icon} {gd['status']:8s} CPU={cpu_str:>7s} Mem={mem_str:>7s}"
            )

    finally:
        conn.close()

    return "\n".join(lines)


def report_gpu(config: Config | None = None, hours: int = 2) -> str:
    """Generate a recent GPU trend report."""
    config = config or get_config()
    conn = _connect_readonly(config)
    lines: list[str] = []

    try:
        rows = conn.execute(
            """SELECT s.ts, h.gpu_name, h.gpu_temp_c, h.gpu_busy_pct,
                      h.gpu_power_w, h.gpu_vram_used_pct
               FROM samples s
               JOIN host_metrics h ON h.sample_id = s.id
               WHERE s.ts >= datetime('now', ?)
                 AND h.gpu_busy_pct IS NOT NULL
               ORDER BY s.ts DESC
               LIMIT 20""",
            (f"-{hours} hours",),
        ).fetchall()

        if not rows:
            return "No GPU data available."

        lines.append(f"=== GPU Trend (last {hours}h, most recent 20 samples) ===")
        lines.append(f"{'Timestamp':20s} {'Name':20s} {'Temp°C':>7s} {'Busy%':>7s} {'PowerW':>7s} {'VRAM%':>7s}")
        lines.append("-" * 70)

        for row in rows:
            d = dict(row)
            lines.append(
                f"{d['ts']:20s} {d['gpu_name'] or '-':20s} "
                f"{d['gpu_temp_c'] or '-':>7} {d['gpu_busy_pct'] or '-':>7} "
                f"{d['gpu_power_w'] or '-':>7} {d['gpu_vram_used_pct'] or '-':>7}"
            )

    finally:
        conn.close()

    return "\n".join(lines)


def _fmt_bytes(b: int | None) -> str:
    """Format bytes into human-readable form."""
    if b is None:
        return "-"
    if b >= 1_073_741_824:
        return f"{b / 1_073_741_824:.1f} GiB"
    if b >= 1_048_576:
        return f"{b / 1_048_576:.1f} MiB"
    if b >= 1024:
        return f"{b / 1024:.1f} KiB"
    return f"{b} B"
