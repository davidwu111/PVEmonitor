"""Tests for rate calculation logic."""

from pvemonitor.rates import compute_guest_rates


class TestComputeGuestRates:
    """Test rate calculation from cumulative counters."""

    def test_normal_rate_calculation(self):
        """Rates should be correctly computed from delta values."""
        current = {
            "diskread_bytes_total": 110_000_000,
            "diskwrite_bytes_total": 55_000_000,
            "netin_bytes_total": 220_000_000,
            "netout_bytes_total": 110_000_000,
            "is_running": 1,
        }
        previous = {
            "diskread_bytes_total": 100_000_000,
            "diskwrite_bytes_total": 50_000_000,
            "netin_bytes_total": 200_000_000,
            "netout_bytes_total": 100_000_000,
            "is_running": 1,
            "epoch_s": 1000,
        }

        rates = compute_guest_rates(current, previous, current_epoch_s=1010)

        assert rates["diskread_bps"] == 1_000_000.0  # 10M / 10s
        assert rates["diskwrite_bps"] == 500_000.0
        assert rates["netin_bps"] == 2_000_000.0
        assert rates["netout_bps"] == 1_000_000.0

    def test_no_previous_sample(self):
        """Should return all None when there's no previous sample."""
        current = {"is_running": 1, "diskread_bytes_total": 100_000_000}
        rates = compute_guest_rates(current, None, current_epoch_s=1000)
        assert all(v is None for v in rates.values())

    def test_stopped_previous(self):
        """Should return None when previous sample was not running."""
        current = {"diskread_bytes_total": 110_000_000, "is_running": 1}
        previous = {"diskread_bytes_total": 100_000_000, "is_running": 0, "epoch_s": 1000}
        rates = compute_guest_rates(current, previous, current_epoch_s=1010)
        assert rates["diskread_bps"] is None

    def test_stopped_current(self):
        """Should return None when current sample is not running."""
        current = {"diskread_bytes_total": 110_000_000, "is_running": 0}
        previous = {"diskread_bytes_total": 100_000_000, "is_running": 1, "epoch_s": 1000}
        rates = compute_guest_rates(current, previous, current_epoch_s=1010)
        assert rates["diskread_bps"] is None

    def test_counter_reset(self):
        """Should return None when current counter < previous (reboot reset)."""
        current = {"diskread_bytes_total": 500_000, "is_running": 1}
        previous = {"diskread_bytes_total": 100_000_000, "is_running": 1, "epoch_s": 1000}
        rates = compute_guest_rates(current, previous, current_epoch_s=1010)
        assert rates["diskread_bps"] is None

    def test_max_interval_guard(self):
        """Should return None when interval exceeds max_interval_s."""
        current = {"diskread_bytes_total": 200_000_000, "is_running": 1}
        previous = {"diskread_bytes_total": 100_000_000, "is_running": 1, "epoch_s": 1000}
        # Interval is 200s, max is 120s
        rates = compute_guest_rates(current, previous, current_epoch_s=1200, max_interval_s=120)
        assert rates["diskread_bps"] is None

    def test_negative_delta_seconds(self):
        """Should return None when time moves backwards (clock skew)."""
        current = {"diskread_bytes_total": 110_000_000, "is_running": 1}
        previous = {"diskread_bytes_total": 100_000_000, "is_running": 1, "epoch_s": 2000}
        rates = compute_guest_rates(current, previous, current_epoch_s=1000)
        assert rates["diskread_bps"] is None

    def test_sanity_ceiling_disk(self):
        """Should suppress unreasonably large disk deltas."""
        # delta = 20 GB in 10s = 2 GB/s — exceeds 500 MB/s * 2 ceiling
        current = {"diskread_bytes_total": 20_000_000_000, "is_running": 1}
        previous = {"diskread_bytes_total": 1_000, "is_running": 1, "epoch_s": 1000}
        rates = compute_guest_rates(current, previous, current_epoch_s=1010)
        assert rates["diskread_bps"] is None

    def test_null_counter_values(self):
        """Should handle None counter values gracefully."""
        current = {"diskread_bytes_total": None, "is_running": 1}
        previous = {"diskread_bytes_total": 100_000_000, "is_running": 1, "epoch_s": 1000}
        rates = compute_guest_rates(current, previous, current_epoch_s=1010)
        assert rates["diskread_bps"] is None

    def test_missing_epoch(self):
        """Should handle missing epoch_s in previous sample."""
        current = {"diskread_bytes_total": 110_000_000, "is_running": 1}
        previous = {"diskread_bytes_total": 100_000_000, "is_running": 1}
        # No epoch_s key
        rates = compute_guest_rates(current, previous, current_epoch_s=1010)
        assert rates["diskread_bps"] is None
