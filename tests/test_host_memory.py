"""Tests for host memory accounting."""

from pvemonitor.host import _collect_memory
import pvemonitor.host as host_module


def test_collect_memory_uses_memavailable_for_user_visible_free(monkeypatch):
    meminfo = """MemTotal:       31603440 kB
MemFree:         1355896 kB
MemAvailable:   17669724 kB
Buffers:         7178160 kB
Cached:          8912028 kB
SReclaimable:     812540 kB
SwapTotal:       8388604 kB
SwapFree:        8356256 kB
"""

    monkeypatch.setattr(host_module, "read_proc_file", lambda path: meminfo if path == "/proc/meminfo" else None)

    metrics = _collect_memory()

    assert metrics["mem_total_bytes"] == 32361922560
    assert metrics["mem_used_bytes"] == 14268125184
    assert metrics["mem_free_bytes"] == 18093797376
    assert metrics["mem_used_bytes"] + metrics["mem_free_bytes"] == metrics["mem_total_bytes"]
    assert metrics["swap_total_bytes"] == 8589930496
    assert metrics["swap_used_bytes"] == 33124352


def test_collect_memory_falls_back_when_memavailable_missing(monkeypatch):
    meminfo = """MemTotal:        1024000 kB
MemFree:          128000 kB
Buffers:           64000 kB
Cached:           256000 kB
SReclaimable:      32000 kB
SwapTotal:         512000 kB
SwapFree:          256000 kB
"""

    monkeypatch.setattr(host_module, "read_proc_file", lambda path: meminfo if path == "/proc/meminfo" else None)

    metrics = _collect_memory()

    assert metrics["mem_total_bytes"] == 1048576000
    assert metrics["mem_used_bytes"] == 557056000
    assert metrics["mem_free_bytes"] == 491520000
    assert metrics["mem_used_bytes"] + metrics["mem_free_bytes"] == metrics["mem_total_bytes"]
    assert metrics["swap_total_bytes"] == 524288000
    assert metrics["swap_used_bytes"] == 262144000
