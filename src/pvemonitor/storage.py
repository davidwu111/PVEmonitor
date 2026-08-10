"""In-memory telemetry store for PVEmonitor.

All telemetry (samples, host/guest metrics, guest identities, rollups, and
collector errors) is held in a single SQLite database that lives entirely in
RAM. The store is guarded by a threading lock so the collection loop and the
API request handlers can share it safely.

Disk activity is limited to optional full snapshots (SQLite backup files)
written on a configurable interval and on shutdown, then loaded back on
startup. A configurable memory cap is enforced by evicting the oldest raw
samples; usage is estimated per row so enforcement is deterministic.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import Config
from .db import ensure_schema

logger = logging.getLogger(__name__)

SNAPSHOT_PREFIX = "telemetry-"
SNAPSHOT_SUFFIX = ".sqlite3"

# Prune down to this fraction of the memory cap so eviction does not churn
# on every single sample.
CAP_TARGET_RATIO = 0.9

# Per-row memory estimates (bytes) used to enforce storage.memory_limit_mb.
# Conservative approximations of each row's share of the SQLite page cache
# (row data plus index overhead). Used only for cap enforcement and health
# reporting, not as an exact RSS measurement.
ROW_BYTES: dict[str, int] = {
    "samples": 160,
    "host_metrics": 384,
    "guest_samples": 384,
    "guests": 128,
    "collector_errors": 256,
    "host_rollups": 384,
    "guest_rollups": 384,
}


def latest_snapshot(snapshot_dir: Path) -> Path | None:
    """Return the newest snapshot file in a directory, or None."""
    files = sorted(snapshot_dir.glob(f"{SNAPSHOT_PREFIX}*{SNAPSHOT_SUFFIX}"))
    return files[-1] if files else None


class _MaterializedRows:
    """Cursor-like view over already-fetched rows.

    Lets existing code keep using fetchone()/fetchall() and iteration without
    holding the store lock while a statement is active.
    """

    def __init__(self, rows: list[sqlite3.Row], rowcount: int | None = None):
        self._rows = rows
        self._rowcount = rowcount
        self._pos = 0

    @property
    def rowcount(self) -> int | None:
        return self._rowcount

    def fetchone(self) -> sqlite3.Row | None:
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchall(self) -> list[sqlite3.Row]:
        rows = self._rows[self._pos:]
        self._pos = len(self._rows)
        return rows

    def __iter__(self) -> Iterator[sqlite3.Row]:
        return iter(self._rows[self._pos:])


class ReadHandle:
    """Read-only handle to the shared in-memory store.

    Mimics the parts of a sqlite3 connection that API/reporting code uses:
    execute() returns a cursor-like object over materialized rows and
    close() is a no-op (the store lives for the lifetime of the process).
    """

    def __init__(self, storage: "Storage"):
        self._storage = storage

    def execute(self, sql: str, parameters: Any = ()) -> _MaterializedRows:
        with self._storage._lock:
            cur = self._storage._conn.execute(sql, parameters)
            rows = cur.fetchall()
            rowcount = cur.rowcount
        return _MaterializedRows(rows, rowcount)

    def executescript(self, sql: str) -> None:
        with self._storage._lock:
            self._storage._conn.executescript(sql)

    def commit(self) -> None:
        with self._storage._lock:
            self._storage._conn.commit()

    def rollback(self) -> None:
        with self._storage._lock:
            self._storage._conn.rollback()

    def close(self) -> None:
        """No-op: the store is process-owned."""


class Storage:
    """Thread-safe in-memory telemetry store with snapshot support."""

    def __init__(
        self,
        config: Config,
        memory_limit_bytes: int | None = None,
    ):
        self.config = config
        self._lock = threading.RLock()
        self._memory_limit_bytes = (
            memory_limit_bytes
            if memory_limit_bytes is not None
            else config.memory_limit_bytes
        )
        self._snapshot_dir = config.snapshot_dir
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._last_snapshot_path: Path | None = None
        self._last_snapshot_at: float = 0.0
        self._conn = self._create_connection()
        ensure_schema(self._conn, config)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Connection / locking
    # ------------------------------------------------------------------

    @staticmethod
    def _create_connection() -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    @contextmanager
    def lock(self) -> Iterator[sqlite3.Connection]:
        """Acquire the store lock and yield the underlying connection."""
        with self._lock:
            yield self._conn

    def read_handle(self) -> ReadHandle:
        """Return a read handle for API/reporting code."""
        return ReadHandle(self)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Memory accounting and cap enforcement
    # ------------------------------------------------------------------

    def estimated_bytes(self, counts: dict[str, int] | None = None) -> int:
        """Estimate the store's memory usage from row counts."""
        if counts is None:
            counts = self.table_counts()
        return sum(counts.get(name, 0) * weight for name, weight in ROW_BYTES.items())

    def table_counts(self) -> dict[str, int]:
        with self._lock:
            return {
                name: self._conn.execute(f"SELECT COUNT(*) AS c FROM {name}").fetchone()["c"]
                for name in ROW_BYTES
            }

    def enforce_memory_cap(self) -> dict[str, int] | None:
        """Evict oldest raw samples until estimated usage fits the cap.

        The newest sample is never evicted. Returns eviction stats, or None
        when no cap is configured.
        """
        limit = self._memory_limit_bytes
        if limit <= 0:
            return None
        target = int(limit * CAP_TARGET_RATIO)
        deleted = 0
        warned = False
        with self._lock:
            for _ in range(500):  # convergence guard
                used = self.estimated_bytes()
                if used <= target:
                    break
                total = self._conn.execute("SELECT COUNT(*) AS c FROM samples").fetchone()["c"]
                if total <= 1:
                    if not warned:
                        logger.warning(
                            "Memory cap of %d MiB is too small for one sample; "
                            "keeping only the newest sample",
                            limit // (1024 * 1024),
                        )
                        warned = True
                    break
                per_sample = max(used / total, 1.0)
                to_delete = min(int((used - target) / per_sample) + 1, total - 1)
                cur = self._conn.execute(
                    "DELETE FROM samples WHERE id IN "
                    "(SELECT id FROM samples ORDER BY id ASC LIMIT ?)",
                    (to_delete,),
                )
                deleted += cur.rowcount or 0
            if deleted:
                self._conn.commit()
        if deleted:
            logger.info(
                "Memory cap evicted %d old samples (limit %d MiB)",
                deleted,
                limit // (1024 * 1024),
            )
        return {"samples_deleted": deleted, "memory_used_bytes": self.estimated_bytes()}

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def maybe_snapshot(self) -> Path | None:
        """Write a snapshot if the configured interval has elapsed."""
        if not self.config.snapshot_enabled:
            return None
        now = time.monotonic()
        if now - self._last_snapshot_at < self.config.snapshot_interval_s:
            return None
        path = self.snapshot()
        if path is not None:
            self._last_snapshot_at = now
        return path

    def snapshot(self, path: Path | None = None) -> Path | None:
        """Write a full backup of the store to disk and return its path."""
        if not self.config.snapshot_enabled:
            return None
        target = path or self._snapshot_dir / (
            f"{SNAPSHOT_PREFIX}{int(time.time())}{SNAPSHOT_SUFFIX}"
        )
        tmp = target.with_name(target.name + ".tmp")
        with self._lock:
            dest = sqlite3.connect(str(tmp))
            try:
                self._conn.backup(dest)
            finally:
                dest.close()
            tmp.replace(target)
        self._prune_old_snapshots()
        self._last_snapshot_path = target
        logger.info("Telemetry snapshot written: %s", target)
        return target

    def load_latest_snapshot(self) -> Path | None:
        """Load the newest snapshot from disk into the store."""
        path = latest_snapshot(self._snapshot_dir)
        if path is None:
            return None
        with self._lock:
            new_conn = self._create_connection()
            src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                src.backup(new_conn)
            finally:
                src.close()
            old, self._conn = self._conn, new_conn
            old.close()
        self._last_snapshot_path = path
        self._last_snapshot_at = time.monotonic()
        logger.info(
            "Loaded telemetry snapshot: %s (%d samples)",
            path,
            self.table_counts()["samples"],
        )
        return path

    def import_legacy_db(self) -> bool:
        """One-time read-only import of the legacy SQLite database."""
        if not self.config.import_legacy_db:
            return False
        db_path = self.config.db_path
        if not db_path.exists():
            return False
        try:
            with self._lock:
                count = self._conn.execute("SELECT COUNT(*) AS c FROM samples").fetchone()["c"]
                if count:
                    return False
                src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                try:
                    src.backup(self._conn)
                finally:
                    src.close()
        except Exception as exc:
            logger.warning("Legacy database import failed (%s): %s", db_path, exc)
            return False
        logger.info("Imported legacy telemetry database: %s", db_path)
        return True

    def _prune_old_snapshots(self) -> None:
        keep = self.config.snapshot_keep
        if keep <= 0:
            return
        files = sorted(self._snapshot_dir.glob(f"{SNAPSHOT_PREFIX}*{SNAPSHOT_SUFFIX}"))
        for old in files[:-keep]:
            try:
                old.unlink()
            except OSError as exc:
                logger.warning("Could not remove old snapshot %s: %s", old, exc)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Return store statistics for health checks."""
        counts = self.table_counts()
        last_snap = self._last_snapshot_path
        now = time.time()
        last_ts = None
        last_age = None
        if last_snap is not None:
            try:
                mtime = last_snap.stat().st_mtime
                last_ts = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
                last_age = int(now - mtime)
            except OSError:
                pass
        return {
            "memory_used_bytes": self.estimated_bytes(counts),
            "memory_limit_bytes": self._memory_limit_bytes,
            "table_counts": counts,
            "last_snapshot_path": str(last_snap) if last_snap else None,
            "last_snapshot_ts": last_ts,
            "last_snapshot_age_s": last_age,
        }
