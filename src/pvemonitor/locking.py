"""Non-overlap collection lock for PVEmonitor.

Prevents systemd timer-triggered collection runs from stacking.
Uses a PID+timestamp lock file with stale lock detection.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _read_lock_file(lock_path: Path) -> tuple[int, float] | None:
    """Read PID and timestamp from an existing lock file.

    Returns (pid, timestamp) or None if unreadable.
    """
    try:
        text = lock_path.read_text().strip()
        parts = text.split()
        if len(parts) >= 2:
            pid = int(parts[0])
            ts = float(parts[1])
            return (pid, ts)
    except (ValueError, OSError):
        pass
    return None


def _pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is still alive."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock(
    lock_path: Path,
    stale_timeout_s: float,
    force: bool = False,
) -> bool:
    """Attempt to acquire the collection lock.

    Lock file creation is atomic via O_CREAT | O_EXCL.

    On stale lock detection (PID dead or lock age > stale_timeout_s):
      logs a warning, removes the stale lock, and acquires a new one.

    Args:
        lock_path: Path to the lock file.
        stale_timeout_s: Seconds after which a lock is considered stale.
        force: If True, remove any existing lock regardless of staleness.

    Returns:
        True if the lock was acquired. False if another collector holds it.
    """
    try:
        # Attempt atomic creation
        fd = os.open(
            str(lock_path),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o644,
        )
        with os.fdopen(fd, "w") as f:
            f.write(f"{os.getpid()} {time.time()}")
        logger.debug("Lock acquired: %s", lock_path)
        return True
    except FileExistsError:
        pass

    # Lock file exists — check staleness
    if force:
        logger.warning("Force flag set: removing existing lock file")
        _remove_lock_file(lock_path)
        return acquire_lock(lock_path, stale_timeout_s, force=False)

    existing = _read_lock_file(lock_path)

    if existing is None:
        # Unreadable lock — remove and retry
        logger.warning("Unreadable lock file; removing")
        _remove_lock_file(lock_path)
        return acquire_lock(lock_path, stale_timeout_s, force=False)

    pid, lock_ts = existing
    lock_age = time.time() - lock_ts

    if not _pid_alive(pid):
        logger.warning(
            "Stale lock detected: PID %d is dead (lock age: %.0fs). Removing and reacquiring.",
            pid, lock_age,
        )
        _remove_lock_file(lock_path)
        return acquire_lock(lock_path, stale_timeout_s, force=False)

    if lock_age > stale_timeout_s:
        logger.warning(
            "Stale lock detected: lock age %.0fs exceeds timeout %.0fs (PID %d alive?). "
            "Removing and reacquiring.",
            lock_age, stale_timeout_s, pid,
        )
        _remove_lock_file(lock_path)
        return acquire_lock(lock_path, stale_timeout_s, force=False)

    # Lock is valid and held by a live process
    logger.info(
        "Another collector is running (PID %d, lock age: %.0fs). Exiting.",
        pid, lock_age,
    )
    return False


def release_lock(lock_path: Path) -> None:
    """Release the collection lock.

    Only removes the lock file if it contains our PID (defense against stealing).
    """
    try:
        text = lock_path.read_text().strip()
        parts = text.split()
        if len(parts) >= 1 and int(parts[0]) == os.getpid():
            lock_path.unlink()
            logger.debug("Lock released: %s", lock_path)
        else:
            logger.warning(
                "Lock file PID mismatch: expected %d, got %s — not removing",
                os.getpid(), parts[0] if parts else "empty",
            )
    except (FileNotFoundError, ValueError, OSError):
        # Lock already gone or unreadable
        pass


def _remove_lock_file(lock_path: Path) -> None:
    """Remove a lock file, swallowing errors."""
    try:
        lock_path.unlink()
    except (FileNotFoundError, OSError):
        pass
