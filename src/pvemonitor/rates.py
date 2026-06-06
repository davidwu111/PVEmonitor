"""Rate calculation for guest disk/network throughput.

Computes bytes-per-second rates by comparing current cumulative counters
to the previous stored sample in SQLite.

Handles:
- Counter resets (current < previous → NULL)
- Unreasonably large deltas (counter bugs → NULL)
- Maximum interval guard (gap too large → NULL)
- First sample for a guest (no previous → NULL)
- Non-running to running transitions (reset interval)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Approximate interface max bytes per second for sanity checks
# 100 Gbps ≈ 12.5 GB/s, we use a generous ceiling
MAX_DISK_BPS = 500_000_000    # 500 MB/s per virtual disk — generous
MAX_NET_BPS = 100_000_000_000 # 100 Gbps — physical ceiling


def compute_guest_rates(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    current_epoch_s: int,
    max_interval_s: int = 120,
) -> dict[str, float | None]:
    """Compute bps rates for disk and network counters.

    Args:
        current: Current guest metrics dict with cumulative counter fields.
        previous: Previous guest_samples row as a dict (from get_previous_guest_sample()),
                  or None if no previous sample exists.
        current_epoch_s: Timestamp of the current sample.
        max_interval_s: Maximum allowed interval for valid rate calculation.

    Returns a dict with keys: diskread_bps, diskwrite_bps, netin_bps, netout_bps.
    Values are floats or None.
    """
    result: dict[str, float | None] = {
        "diskread_bps": None,
        "diskwrite_bps": None,
        "netin_bps": None,
        "netout_bps": None,
    }

    if previous is None:
        return result

    # Check if previous sample was a running sample
    if not previous.get("is_running"):
        return result

    # Check if current sample is a running sample
    if not current.get("is_running"):
        return result

    prev_epoch = previous.get("epoch_s")
    if prev_epoch is None:
        return result

    delta_s = current_epoch_s - prev_epoch
    if delta_s <= 0:
        # Clock skew or duplicate sample — can't compute rate
        return result

    # Max interval guard
    if delta_s > max_interval_s:
        logger.debug(
            "Rate suppressed for guest %s/%s: interval %.0fs exceeds max %ds",
            current.get("vmid"), current.get("guest_type"),
            delta_s, max_interval_s,
        )
        return result

    # Compute rates for each counter pair
    counter_pairs = [
        ("diskread_bytes_total", "diskread_bps", MAX_DISK_BPS),
        ("diskwrite_bytes_total", "diskwrite_bps", MAX_DISK_BPS),
        ("netin_bytes_total", "netin_bps", MAX_NET_BPS),
        ("netout_bytes_total", "netout_bps", MAX_NET_BPS),
    ]

    for counter_key, rate_key, ceiling in counter_pairs:
        cur_val = _get_int(current.get(counter_key))
        prev_val = _get_int(previous.get(counter_key))

        if cur_val is None or prev_val is None:
            continue

        if cur_val < prev_val:
            # Counter reset (reboot, migration)
            logger.debug(
                "%s counter reset for %s/%s (%d → %d)",
                counter_key, current.get("vmid"), current.get("guest_type"),
                prev_val, cur_val,
            )
            continue

        delta_bytes = cur_val - prev_val

        # Sanity check: is the delta physically possible?
        if delta_bytes > ceiling * delta_s * 2:  # generous 2x margin
            logger.debug(
                "%s delta unreasonably large for %s/%s: %d bytes in %.0fs (ceiling %d)",
                counter_key, current.get("vmid"), current.get("guest_type"),
                delta_bytes, delta_s, ceiling,
            )
            continue

        result[rate_key] = round(delta_bytes / delta_s, 2)

    return result


def _get_int(value: Any) -> int | None:
    """Convert a value to int, returning None for None or non-numeric."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None
