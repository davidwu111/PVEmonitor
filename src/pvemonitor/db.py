"""Database layer for PVEmonitor.

Manages SQLite connections, schema migrations, and insert helpers.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from .config import Config

logger = logging.getLogger(__name__)

# PRAGMAs applied to every new connection
CONNECTION_PRAGMAS: list[str] = [
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA temp_store=MEMORY",
    "PRAGMA busy_timeout=5000",
]

# Sample-driven periodic WAL checkpoint


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """Create a new SQLite connection with recommended PRAGMAs."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def get_readonly_connection(db_path: str | Path) -> sqlite3.Connection:
    """Create a read-only SQLite connection (for the API server)."""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------

def _get_migration_files(schema_dir: Path) -> list[Path]:
    """Return sorted list of .sql migration files from the schema directory."""
    if not schema_dir.exists():
        return []
    files = sorted(schema_dir.glob("*.sql"))
    return files


def _applied_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied migration version, or -1 if none."""
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        val = row[0]
        return val if val is not None else -1
    except sqlite3.OperationalError:
        return -1


def _apply_migration(conn: sqlite3.Connection, filepath: Path) -> None:
    """Apply a single migration file within its own transaction."""
    version_str = filepath.stem.split("_")[0]
    try:
        version = int(version_str)
    except ValueError:
        logger.warning("Skipping migration with unparseable version: %s", filepath.name)
        return

    sql = filepath.read_text()
    conn.executescript(sql)
    conn.execute(
        "INSERT OR REPLACE INTO schema_version (version, filename) VALUES (?, ?)",
        (version, filepath.name),
    )
    logger.info("Applied migration %d: %s", version, filepath.name)


def ensure_schema(conn: sqlite3.Connection, config: Config) -> None:
    """Create or upgrade the database schema.

    Safe to call repeatedly — only unapplied migrations are run.
    """
    schema_dir = config.home / "sql" / "schema"
    if not schema_dir.exists():
        logger.warning("Schema directory not found: %s", schema_dir)
        return

    applied = _applied_version(conn)
    migration_files = _get_migration_files(schema_dir)

    for fpath in migration_files:
        version_str = fpath.stem.split("_")[0]
        try:
            version = int(version_str)
        except ValueError:
            continue
        if version > applied:
            _apply_migration(conn, fpath)
            conn.commit()


# ---------------------------------------------------------------------------
# Insert helpers
# ---------------------------------------------------------------------------

def insert_sample(
    conn: sqlite3.Connection,
    ts: str,
    epoch_s: int,
    hostname: str,
    collector_version: str,
    collection_ms: int,
    error_count: int = 0,
) -> int:
    """Insert a sample row and return its ID."""
    cur = conn.execute(
        """INSERT INTO samples (ts, epoch_s, hostname, collector_version, collection_ms, error_count)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (ts, epoch_s, hostname, collector_version, collection_ms, error_count),
    )
    return cur.lastrowid


def insert_host_metrics(conn: sqlite3.Connection, sample_id: int, metrics: dict[str, Any]) -> None:
    """Insert or replace a host_metrics row.

    The metrics dict keys should match the host_metrics column names.
    """
    columns = list(metrics.keys())
    placeholders = ", ".join("?" for _ in columns)
    values = [metrics.get(c) for c in columns]
    cols_str = ", ".join(columns)
    conn.execute(
        f"INSERT OR REPLACE INTO host_metrics (sample_id, {cols_str}) VALUES (?, {placeholders})",
        [sample_id] + values,
    )


def upsert_guest(
    conn: sqlite3.Connection,
    vmid: int,
    guest_type: str,
    ts: str,
    name: str | None,
) -> None:
    """Insert or update a guest identity row."""
    conn.execute(
        """INSERT INTO guests (vmid, guest_type, first_seen_ts, last_seen_ts, current_name)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(vmid, guest_type) DO UPDATE SET
               last_seen_ts = excluded.last_seen_ts,
               current_name = excluded.current_name""",
        (vmid, guest_type, ts, ts, name),
    )


def insert_guest_sample(
    conn: sqlite3.Connection,
    sample_id: int,
    vmid: int,
    guest_type: str,
    metrics: dict[str, Any],
) -> None:
    """Insert a guest_samples row."""
    conn.execute(
        """INSERT OR REPLACE INTO guest_samples (
               sample_id, vmid, guest_type, name, status, is_running,
               uptime_s, maxcpu, cpu_host_pct, cpu_of_allocated_pct,
               mem_bytes, maxmem_bytes, mem_pct, swap_bytes,
               diskread_bytes_total, diskwrite_bytes_total,
               netin_bytes_total, netout_bytes_total,
               diskread_bps, diskwrite_bps, netin_bps, netout_bps,
               pressurecpusome, pressurecpufull,
               pressureiosome, pressureiofull,
               pressurememorysome, pressurememoryfull,
               freemem_bytes, balloon_actual_bytes, guest_agent_enabled
           ) VALUES (
               ?, ?, ?, ?, ?, ?,
               ?, ?, ?, ?,
               ?, ?, ?, ?,
               ?, ?, ?, ?,
               ?, ?, ?, ?,
               ?, ?, ?, ?,
               ?, ?, ?, ?, ?
           )""",
        (
            sample_id, vmid, guest_type,
            metrics.get("name"),
            metrics.get("status"),
            metrics.get("is_running", 0),
            metrics.get("uptime_s"),
            metrics.get("maxcpu"),
            metrics.get("cpu_host_pct"),
            metrics.get("cpu_of_allocated_pct"),
            metrics.get("mem_bytes"),
            metrics.get("maxmem_bytes"),
            metrics.get("mem_pct"),
            metrics.get("swap_bytes"),
            metrics.get("diskread_bytes_total"),
            metrics.get("diskwrite_bytes_total"),
            metrics.get("netin_bytes_total"),
            metrics.get("netout_bytes_total"),
            metrics.get("diskread_bps"),
            metrics.get("diskwrite_bps"),
            metrics.get("netin_bps"),
            metrics.get("netout_bps"),
            metrics.get("pressurecpusome"),
            metrics.get("pressurecpufull"),
            metrics.get("pressureiosome"),
            metrics.get("pressureiofull"),
            metrics.get("pressurememorysome"),
            metrics.get("pressurememoryfull"),
            metrics.get("freemem_bytes"),
            metrics.get("balloon_actual_bytes"),
            metrics.get("guest_agent_enabled"),
        ),
    )


def insert_error(
    conn: sqlite3.Connection,
    sample_id: int,
    scope: str,
    message: str,
    vmid: int | None = None,
    guest_type: str | None = None,
) -> None:
    """Insert a collector error row."""
    conn.execute(
        """INSERT INTO collector_errors (sample_id, scope, vmid, guest_type, message)
           VALUES (?, ?, ?, ?, ?)""",
        (sample_id, scope, vmid, guest_type, message),
    )


def get_previous_guest_sample(
    conn: sqlite3.Connection,
    vmid: int,
    guest_type: str,
) -> dict[str, Any] | None:
    """Return the most recent guest_samples row for a given vmid/type.

    Used for rate calculation (comparing current counters to previous).
    """
    row = conn.execute(
        """SELECT gs.*, s.epoch_s
           FROM guest_samples gs
           JOIN samples s ON s.id = gs.sample_id
           WHERE gs.vmid = ? AND gs.guest_type = ?
           ORDER BY s.epoch_s DESC
           LIMIT 1""",
        (vmid, guest_type),
    ).fetchone()

    if row is None:
        return None
    return dict(row)


def do_wal_checkpoint(conn: sqlite3.Connection, mode: str = "PASSIVE") -> None:
    """Execute a WAL checkpoint."""
    try:
        conn.execute(f"PRAGMA wal_checkpoint({mode})")
    except sqlite3.OperationalError as exc:
        logger.warning("WAL checkpoint failed: %s", exc)


def maybe_checkpoint(conn: sqlite3.Connection, sample_id: int | None = None) -> None:
    """Run periodic WAL checkpoint every 60 collected samples."""
    if sample_id is None:
        return
    if sample_id % 60 == 0:
        do_wal_checkpoint(conn, "PASSIVE")
        logger.debug("WAL checkpoint (passive) at sample_id=%d", sample_id)
