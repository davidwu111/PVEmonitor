"""Tests for collection lock acquisition and release."""

import os
import time
from pathlib import Path

import pytest
from pvemonitor.locking import acquire_lock, release_lock, _read_lock_file, _pid_alive


class TestLockAcquisition:
    """Test lock acquire/release behavior."""

    @pytest.fixture
    def lock_path(self, tmp_path: Path) -> Path:
        return tmp_path / "collector.lock"

    def test_acquire_new_lock(self, lock_path: Path):
        """Should acquire a new lock when none exists."""
        result = acquire_lock(lock_path, stale_timeout_s=60)
        assert result is True
        assert lock_path.exists()

        # Verify PID is written
        pid, ts = _read_lock_file(lock_path)
        assert pid == os.getpid()
        assert ts > 0

    def test_release_own_lock(self, lock_path: Path):
        """Should release lock that contains our PID."""
        acquire_lock(lock_path, stale_timeout_s=60)
        release_lock(lock_path)
        assert not lock_path.exists()

    def test_second_collector_blocked(self, lock_path: Path):
        """Second acquisition should fail when lock is held."""
        acquire_lock(lock_path, stale_timeout_s=60)
        # Try to acquire again (simulates a second collector)
        result = acquire_lock(lock_path, stale_timeout_s=60)
        assert result is False

    def test_stale_lock_dead_pid(self, lock_path: Path):
        """Should break a stale lock held by a dead PID."""
        # Write a lock with a PID that doesn't exist
        lock_path.write_text(f"99999999 {time.time() - 10}")
        result = acquire_lock(lock_path, stale_timeout_s=60)
        # PID 99999999 should be dead, so we acquire
        assert result is True
        assert lock_path.exists()

    def test_stale_lock_timeout(self, lock_path: Path):
        """Should break a lock that's too old, even if PID is alive-ish."""
        # Write a lock with our own PID but an old timestamp
        lock_path.write_text(f"{os.getpid()} {time.time() - 300}")
        result = acquire_lock(lock_path, stale_timeout_s=60)
        # Our PID is alive but lock is too old — should break
        assert result is True

    def test_fresh_lock_blocks(self, lock_path: Path):
        """A fresh lock with our own PID should block reacquisition."""
        # First acquire
        acquire_lock(lock_path, stale_timeout_s=60)
        # Try again immediately — should block
        result = acquire_lock(lock_path, stale_timeout_s=60)
        assert result is False

    def test_force_override(self, lock_path: Path):
        """--force should override any existing lock."""
        # Pre-populate a lock
        lock_path.write_text(f"99999999 {time.time()}")
        result = acquire_lock(lock_path, stale_timeout_s=60, force=True)
        assert result is True
        assert lock_path.exists()

    def test_unreadable_lock_file(self, lock_path: Path):
        """Should handle unreadable/corrupt lock files gracefully."""
        lock_path.write_text("garbage no pid")
        result = acquire_lock(lock_path, stale_timeout_s=60)
        assert result is True

    def test_release_after_exception(self, lock_path: Path):
        """Lock should be released in finally block pattern."""
        acquired = acquire_lock(lock_path, stale_timeout_s=60)
        assert acquired

        # Simulate finally block
        release_lock(lock_path)
        assert not lock_path.exists()

    def test_release_wrong_pid_defense(self, lock_path: Path):
        """release_lock should not remove a lock file with a different PID."""
        acquire_lock(lock_path, stale_timeout_s=60)

        # Simulate another process overwriting the lock
        lock_path.write_text("12345 999999999.0")

        # release_lock should detect PID mismatch and NOT remove
        release_lock(lock_path)
        assert lock_path.exists()  # Lock should still exist


class TestPidAlive:
    """Test PID liveness check."""

    def test_own_pid_alive(self):
        """Our own PID should be alive."""
        assert _pid_alive(os.getpid()) is True

    def test_nonexistent_pid(self):
        """A PID of 99999999 should not be alive."""
        assert _pid_alive(99999999) is False


class TestReadLockFile:
    """Test lock file parsing."""

    def test_valid_lock(self, tmp_path: Path):
        lock_f = tmp_path / "test.lock"
        lock_f.write_text("12345 9999999999.123")
        pid, ts = _read_lock_file(lock_f)
        assert pid == 12345
        assert ts == 9999999999.123

    def test_missing_file(self, tmp_path: Path):
        lock_f = tmp_path / "nonexistent.lock"
        result = _read_lock_file(lock_f)
        assert result is None

    def test_corrupt_content(self, tmp_path: Path):
        lock_f = tmp_path / "bad.lock"
        lock_f.write_text("not a valid lock file")
        result = _read_lock_file(lock_f)
        assert result is None
