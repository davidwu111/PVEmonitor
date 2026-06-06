"""Tests for schema creation and migration."""

import sqlite3

import pytest


class TestSchemaCreation:
    """Verify that all expected tables and indexes exist after migration."""

    def test_all_tables_exist(self, temp_db):
        """All core tables should exist."""
        tables = {
            row[0]
            for row in temp_db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        }
        expected = {
            "schema_version",
            "samples",
            "host_metrics",
            "host_rollups",
            "guests",
            "guest_samples",
            "guest_rollups",
            "collector_errors",
        }
        assert expected.issubset(tables)

    def test_schema_version_table(self, temp_db):
        """schema_version table should exist and be empty initially."""
        row = temp_db.execute("SELECT MAX(version) FROM schema_version").fetchone()
        assert row[0] is None  # No migrations tracked in test (applied from files directly)

    def test_samples_columns(self, temp_db):
        """samples table should have the expected columns."""
        cols = _get_columns(temp_db, "samples")
        expected = {"id", "ts", "epoch_s", "hostname", "collector_version", "collection_ms", "error_count"}
        assert expected.issubset(cols)

    def test_host_metrics_columns(self, temp_db):
        """host_metrics should have all required columns."""
        cols = _get_columns(temp_db, "host_metrics")
        required = {
            "sample_id", "load1", "load5", "load15",
            "cpu_usage_pct", "cpu_user_pct", "cpu_system_pct", "cpu_iowait_pct", "cpu_idle_pct",
            "cpu_temp_c",
            "mem_total_bytes", "mem_used_bytes", "mem_free_bytes",
            "swap_total_bytes", "swap_used_bytes",
            "rootfs_total_bytes", "rootfs_used_bytes", "rootfs_free_bytes",
            "gpu_name", "gpu_busy_pct", "gpu_temp_c", "gpu_power_w", "gpu_vram_used_pct",
        }
        assert required.issubset(cols)

    def test_guest_samples_columns(self, temp_db):
        """guest_samples should have all required columns."""
        cols = _get_columns(temp_db, "guest_samples")
        required = {
            "sample_id", "vmid", "guest_type", "name", "status", "is_running",
            "uptime_s", "maxcpu", "cpu_host_pct", "cpu_of_allocated_pct",
            "mem_bytes", "maxmem_bytes", "mem_pct",
            "diskread_bytes_total", "diskwrite_bytes_total",
            "netin_bytes_total", "netout_bytes_total",
            "diskread_bps", "diskwrite_bps", "netin_bps", "netout_bps",
        }
        assert required.issubset(cols)

    def test_indexes_exist(self, temp_db):
        """Expected indexes should exist."""
        indexes = {
            row[0]
            for row in temp_db.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        expected = {
            "idx_samples_ts",
            "idx_samples_epoch",
            "idx_host_rollups_resolution_epoch",
            "idx_guest_samples_vmid_type_sample",
            "idx_guest_samples_status",
            "idx_guest_rollups_vmid_type_resolution_epoch",
        }
        assert expected.issubset(indexes)

    def test_foreign_keys_enabled(self, temp_db):
        """PRAGMA foreign_keys should be ON."""
        row = temp_db.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1

    def test_views_exist(self, temp_db):
        """Expected views should exist."""
        views = {
            row[0]
            for row in temp_db.execute(
                "SELECT name FROM sqlite_master WHERE type='view'"
            ).fetchall()
        }
        expected = {"latest_sample", "current_guest_status", "host_metrics_ts"}
        assert expected.issubset(views)

    def test_cascade_delete_from_samples(self, temp_db):
        """Deleting a sample should cascade to host_metrics and guest_samples."""
        # Insert a sample with host_metrics and a guest_sample
        temp_db.execute(
            "INSERT INTO samples (ts, epoch_s, hostname) VALUES ('2026-06-06T00:00:00Z', 1000, 'pve')"
        )
        sample_id = temp_db.execute("SELECT last_insert_rowid()").fetchone()[0]

        temp_db.execute(
            "INSERT INTO host_metrics (sample_id, cpu_usage_pct) VALUES (?, 50.0)",
            (sample_id,),
        )
        temp_db.execute(
            "INSERT INTO guests (vmid, guest_type, first_seen_ts, last_seen_ts) "
            "VALUES (1, 'qemu', '2026-06-06T00:00:00Z', '2026-06-06T00:00:00Z')"
        )
        temp_db.execute(
            "INSERT INTO guest_samples (sample_id, vmid, guest_type, status, is_running) "
            "VALUES (?, 1, 'qemu', 'running', 1)",
            (sample_id,),
        )
        temp_db.commit()

        # Delete the sample
        temp_db.execute("DELETE FROM samples WHERE id = ?", (sample_id,))
        temp_db.commit()

        # Verify cascades
        host = temp_db.execute(
            "SELECT COUNT(*) FROM host_metrics WHERE sample_id = ?", (sample_id,)
        ).fetchone()[0]
        assert host == 0

        guest = temp_db.execute(
            "SELECT COUNT(*) FROM guest_samples WHERE sample_id = ?", (sample_id,)
        ).fetchone()[0]
        assert guest == 0


def _get_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Get column names for a table."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}
