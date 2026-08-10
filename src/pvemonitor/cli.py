"""Command-line interface for PVEmonitor.

Subcommands:
    collect         One full collection run
    init-db         Initialize or upgrade schema
    serve           Start FastAPI server
    report-latest   Latest host + guest status summary
    report-gpu      Recent GPU trend
    health          Health check (exit 0/1/2)
    config          Print resolved configuration
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any


def _get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pvemonitor",
        description="PVEmonitor — Proxmox VE host and guest monitor",
    )
    sub = parser.add_subparsers(dest="command", help="Subcommand")

    # collect
    collect_parser = sub.add_parser("collect", help="Run one full collection")
    collect_parser.add_argument(
        "--force", action="store_true",
        help="Accepted for CLI compatibility (no longer needed — no lock file)",
    )
    collect_parser.add_argument(
        "--config", type=str, default=None,
        help="Path to monitor.yaml",
    )

    # init-db
    init_parser = sub.add_parser("init-db", help="Initialize or upgrade database schema")
    init_parser.add_argument(
        "--config", type=str, default=None,
        help="Path to monitor.yaml",
    )

    # serve
    serve_parser = sub.add_parser("serve", help="Start FastAPI server")
    serve_parser.add_argument(
        "--config", type=str, default=None,
        help="Path to monitor.yaml",
    )
    serve_parser.add_argument(
        "--host", type=str, default=None,
        help="Bind address (overrides config)",
    )
    serve_parser.add_argument(
        "--port", type=int, default=None,
        help="Bind port (overrides config)",
    )
    serve_parser.add_argument(
        "--reload", action="store_true",
        help="Enable uvicorn auto-reload (development only)",
    )

    # report-latest
    report_latest_parser = sub.add_parser("report-latest", help="Latest host + guest status")
    report_latest_parser.add_argument(
        "--config", type=str, default=None,
        help="Path to monitor.yaml",
    )

    # report-gpu
    report_gpu_parser = sub.add_parser("report-gpu", help="Recent GPU trend")
    report_gpu_parser.add_argument(
        "--config", type=str, default=None,
        help="Path to monitor.yaml",
    )
    report_gpu_parser.add_argument(
        "--hours", type=int, default=2,
        help="Hours of history to show (default: 2)",
    )

    # health
    health_parser = sub.add_parser("health", help="Health check")
    health_parser.add_argument(
        "--config", type=str, default=None,
        help="Path to monitor.yaml",
    )
    health_parser.add_argument(
        "--json", action="store_true",
        help="Output JSON instead of text",
    )

    # config
    config_parser = sub.add_parser("config", help="Print resolved configuration")
    config_parser.add_argument(
        "--config", type=str, default=None,
        help="Path to monitor.yaml",
    )

    return parser


def main(argv: list[str] | None = None) -> None:
    """Entry point for CLI."""
    parser = _get_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    # Import heavy modules only when needed
    if args.command == "collect":
        _cmd_collect(args)
    elif args.command == "init-db":
        _cmd_init_db(args)
    elif args.command == "serve":
        _cmd_serve(args)
    elif args.command == "report-latest":
        _cmd_report_latest(args)
    elif args.command == "report-gpu":
        _cmd_report_gpu(args)
    elif args.command == "health":
        _cmd_health(args)
    elif args.command == "config":
        _cmd_config(args)
    else:
        parser.print_help()
        sys.exit(1)


def _cmd_collect(args: Any) -> None:
    from .collector import run_collection
    from .config import get_config

    config = get_config(args.config)
    errors = run_collection(config, force=args.force)
    sys.exit(min(errors, 127))


def _cmd_init_db(args: Any) -> None:
    from .config import get_config

    config = get_config(args.config)
    config.snapshot_dir.mkdir(parents=True, exist_ok=True)
    print("Telemetry is stored in memory — schema is created automatically on `serve`.")
    print(f"Runtime directories ready under {config.home}")


def _cmd_serve(args: Any) -> None:
    import uvicorn

    from .config import get_config
    from .api import create_app

    config = get_config(args.config)
    host = args.host or config.api_host
    port = args.port or config.api_port

    app = create_app(config)
    uvicorn.run(app, host=host, port=port, reload=args.reload)


def _cmd_report_latest(args: Any) -> None:
    from .config import get_config
    from .reporting import report_latest

    config = get_config(args.config)
    print(report_latest(config))


def _cmd_report_gpu(args: Any) -> None:
    from .config import get_config
    from .reporting import report_gpu

    config = get_config(args.config)
    print(report_gpu(config, hours=args.hours))


def _cmd_health(args: Any) -> None:
    import json
    import time

    from .config import get_config

    config = get_config(args.config)

    checks: dict = {"status": "ok", "checks": {}}

    # Check 1: live API (authoritative when the service is running)
    api_health = _fetch_api_health(config)
    if api_health is not None:
        checks["status"] = api_health.get("status", "degraded")
        checks["checks"]["api"] = {
            "status": api_health.get("status", "degraded"),
            "latest_sample_age_s": api_health.get("latest_sample_age_s"),
            "total_samples": api_health.get("total_samples"),
            "memory_used_bytes": api_health.get("memory_used_bytes"),
            "memory_limit_bytes": api_health.get("memory_limit_bytes"),
        }
    else:
        # Fallback: newest snapshot on disk
        from .storage import latest_snapshot

        snap = latest_snapshot(config.snapshot_dir)
        if snap is None:
            checks["status"] = "dead"
            checks["checks"]["store"] = "dead — no telemetry snapshot yet"
        else:
            from .reporting import _open_latest_snapshot

            try:
                conn = _open_latest_snapshot(config)
                if conn is None:
                    raise ValueError("snapshot unreadable")
                row = conn.execute("SELECT MAX(id), MAX(epoch_s) FROM samples").fetchone()
                conn.close()
                latest_epoch = row[1] if row is not None and row[1] is not None else 0
                age_s = int(time.time()) - latest_epoch
                snap_age_s = int(time.time()) - int(snap.stat().st_mtime)

                checks["checks"]["store"] = {
                    "status": "ok",
                    "latest_sample_age_s": age_s,
                    "snapshot_age_s": snap_age_s,
                    "snapshot_path": str(snap),
                }
                if age_s > (config.sample_interval_s * 5):
                    checks["checks"]["store"]["status"] = "degraded"
                    checks["status"] = "degraded"
                elif snap_age_s > (config.snapshot_interval_s * 3):
                    checks["checks"]["store"]["status"] = "degraded"
                    checks["status"] = "degraded"
            except Exception:
                checks["status"] = "dead"
                checks["checks"]["store"] = f"dead — snapshot unreadable: {snap}"

    if args.json:
        print(json.dumps(checks, indent=2))
    else:
        status_icon = {"ok": "✓", "degraded": "⚠", "dead": "✗"}.get(checks["status"], "?")
        print(f"{status_icon} PVEmonitor health: {checks['status']}")
        for check_name, check_result in checks["checks"].items():
            if isinstance(check_result, dict):
                print(f"  {check_name}: {check_result.get('status', 'unknown')}")
            else:
                print(f"  {check_name}: {check_result}")

    exit_code = {"ok": 0, "degraded": 1, "dead": 2}.get(checks["status"], 2)
    sys.exit(exit_code)


def _fetch_api_health(config: Any) -> dict | None:
    """Query the live API health endpoint, returning None if unreachable."""
    import json
    import urllib.request

    url = f"http://127.0.0.1:{config.api_port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _cmd_config(args: Any) -> None:
    import json

    from .config import get_config

    config = get_config(args.config)
    d = config.as_dict()
    # Convert paths to strings for JSON serialization
    print(json.dumps(d, indent=2, default=str))
