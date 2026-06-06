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
        help="Force lock acquisition (skip stale checks)",
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
    from .db import ensure_schema, get_connection

    config = get_config(args.config)
    conn = get_connection(str(config.db_path))
    try:
        ensure_schema(conn, config)
        conn.commit()
        print(f"Database schema initialized at {config.db_path}")
    finally:
        conn.close()


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
    import os

    from .config import get_config
    from .db import get_readonly_connection

    config = get_config(args.config)
    lock_path = config.lock_path

    checks: dict = {"status": "ok", "checks": {}}

    # Check 1: DB readable
    try:
        conn = get_readonly_connection(str(config.db_path))
        row = conn.execute("SELECT MAX(id), MAX(epoch_s) FROM samples").fetchone()
        latest_id = row[0]
        latest_epoch = row[1]
        conn.close()

        if latest_id is None:
            checks["status"] = "degraded"
            checks["checks"]["db"] = "degraded — no samples"
        else:
            import time
            age_s = int(time.time()) - latest_epoch
            checks["checks"]["db"] = {
                "status": "ok",
                "latest_sample_id": latest_id,
                "latest_sample_age_s": age_s,
            }
            if age_s > (config.sample_interval_s * 5):
                checks["checks"]["db"]["status"] = "degraded"
                checks["status"] = "degraded"
    except Exception as exc:
        checks["status"] = "dead"
        checks["checks"]["db"] = f"dead — {exc}"

    # Check 2: Lock file not stale
    if lock_path.exists():
        from .locking import _read_lock_file, _pid_alive
        existing = _read_lock_file(lock_path)
        if existing:
            pid, lock_ts = existing
            if not _pid_alive(pid):
                checks["checks"]["lock"] = "stale — PID dead"
                if checks["status"] == "ok":
                    checks["status"] = "degraded"
            else:
                checks["checks"]["lock"] = "ok — locked by live PID"
        else:
            checks["checks"]["lock"] = "ok — no lock"
    else:
        checks["checks"]["lock"] = "ok — no lock"

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


def _cmd_config(args: Any) -> None:
    import json

    from .config import get_config

    config = get_config(args.config)
    d = config.as_dict()
    # Convert paths to strings for JSON serialization
    print(json.dumps(d, indent=2, default=str))
