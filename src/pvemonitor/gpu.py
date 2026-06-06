"""GPU metrics collection for PVEmonitor.

Primary source: rocm-smi for AMD GPUs.
Fallback: sysfs for basic busy% and temperature.

v1 limitation: Only the first GPU's metrics are stored in host_metrics.
Multi-GPU support is planned for Phase 2.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .config import Config
from .util import (
    SubprocessError,
    parse_float,
    parse_int,
    read_sysfs_file,
    run_cmd,
)

logger = logging.getLogger(__name__)

# Fields reported by rocm-smi that may be unsupported (store NULL)
ROCM_UNSUPPORTED_MARKERS = [
    "N/A",
    "unsupported",
    "Unknown error",
]


def collect_gpu_metrics(config: Config) -> dict[str, Any]:
    """Collect GPU metrics. Tries rocm-smi first, falls back to sysfs.

    Returns a dict of host_metrics GPU columns (gpu_name, gpu_busy_pct,
    gpu_temp_c, gpu_power_w, gpu_vram_used_pct), with NULL defaults.
    """
    result: dict[str, Any] = {
        "gpu_name": None,
        "gpu_busy_pct": None,
        "gpu_temp_c": None,
        "gpu_power_w": None,
        "gpu_vram_used_pct": None,
    }

    # Try rocm-smi
    try:
        metrics = _collect_rocm_smi(config.rocm_smi_bin)
        if metrics:
            result.update(metrics)
            return result
    except SubprocessError:
        logger.debug("rocm-smi failed, trying sysfs fallback")

    # Sysfs fallback
    if config.gpu_sysfs_fallback:
        try:
            metrics = _collect_gpu_sysfs()
            if metrics and any(v is not None for v in metrics.values()):
                result.update(metrics)
        except Exception as exc:
            logger.warning("GPU sysfs collection failed: %s", exc)

    return result


def _collect_rocm_smi(bin_path: str) -> dict[str, Any]:
    """Collect GPU metrics from rocm-smi.

    Uses `rocm-smi --showuse --showtemp --showpower --showmeminfo vram --json`.
    Falls back to individual show commands if --json is not available.
    """
    result: dict[str, Any] = {
        "gpu_name": None,
        "gpu_busy_pct": None,
        "gpu_temp_c": None,
        "gpu_power_w": None,
        "gpu_vram_used_pct": None,
    }

    # Attempt JSON output (preferred: single call, structured)
    import json

    try:
        stdout = run_cmd(
            [
                bin_path,
                "--showuse",
                "--showtemp",
                "--showpower",
                "--showmeminfo", "vram",
                "--json",
            ],
            timeout=5.0,
        )
        if stdout:
            data = json.loads(stdout)
            return _parse_rocm_json(data)
    except (SubprocessError, json.JSONDecodeError):
        pass

    # Fallback: text-mode rocm-smi (parsed output)
    try:
        stdout = run_cmd(
            [bin_path, "--showuse", "--showtemp", "--showpower"],
            timeout=5.0,
        )
        if stdout:
            text_result = _parse_rocm_text(stdout)
            if text_result:
                result.update(text_result)
                return result
    except SubprocessError:
        pass

    return result


def _parse_rocm_json(data: dict | list) -> dict[str, Any]:
    """Parse rocm-smi JSON output into host_metrics GPU fields.

    Handles both dict (single GPU) and list (multi-GPU) top-level shapes.
    v1: stores only the first GPU; logs a warning if multiple GPUs are found.
    """
    result: dict[str, Any] = {
        "gpu_name": None,
        "gpu_busy_pct": None,
        "gpu_temp_c": None,
        "gpu_power_w": None,
        "gpu_vram_used_pct": None,
    }

    # Normalize to list of cards
    cards: list[dict] = []
    if isinstance(data, dict):
        # Try common rocm-smi JSON shapes
        for key in ("cards", "gpus", "GPU"):
            if key in data:
                val = data[key]
                if isinstance(val, list):
                    cards = val
                elif isinstance(val, dict):
                    cards = list(val.values())
                break
        if not cards and "card" in data:
            cards = [data["card"]]
        if not cards:
            # Maybe the top-level dict IS a single card keyed by index
            for k, v in data.items():
                if isinstance(v, dict) and "GPU" in k:
                    cards.append(v)
            if not cards and data:
                # Only treat the dict as a card if it's non-empty
                cards = [data]
    elif isinstance(data, list):
        cards = data

    if not cards:
        return result

    if len(cards) > 1:
        logger.warning(
            "Multiple GPUs detected (%d). v1 stores only the first GPU's metrics.",
            len(cards),
        )

    card = cards[0]
    if isinstance(card, dict):
        result["gpu_name"] = str(card.get("GPU", card.get("Card name", card.get("card", "GPU 0"))))
        # Busy %
        use = card.get("GPU use (%)", card.get("GPU utilization (%)", card.get("busy_percent")))
        result["gpu_busy_pct"] = _safe_float(use)
        # Temperature
        temp = card.get("Temperature (Sensor edge) (C)", card.get("Temperature (C)", card.get("temp")))
        result["gpu_temp_c"] = _safe_float(temp)
        # Power
        power = card.get("Average Graphics Package Power (W)", card.get("Power (W)", card.get("power")))
        result["gpu_power_w"] = _safe_float(power)
        # VRAM (try various keys)
        vram_pct = card.get("VRAM (%)", card.get("vram_percent", card.get("vram_used_pct")))
        if vram_pct is None:
            used = card.get("VRAM Total Used Memory (B)", card.get("vram_used_bytes"))
            total = card.get("VRAM Total Memory (B)", card.get("vram_total_bytes"))
            vram_pct = _compute_vram_pct(used, total)
        result["gpu_vram_used_pct"] = _safe_float(vram_pct)

    return result


def _parse_rocm_text(stdout: str) -> dict[str, Any]:
    """Parse text-mode rocm-smi output (non-JSON fallback).

    Example lines:
        GPU[0]  : 45.0 C
        GPU[0]  : 12.0 %
    """
    result: dict[str, Any] = {
        "gpu_name": None,
        "gpu_busy_pct": None,
        "gpu_temp_c": None,
        "gpu_power_w": None,
        "gpu_vram_used_pct": None,
    }

    lines = stdout.splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue

        # temperature
        match = re.search(r"temperature.*?([\d.]+)\s*C", line, re.IGNORECASE)
        if match:
            result["gpu_temp_c"] = parse_float(match.group(1))
            continue

        # GPU use / busy
        match = re.search(r"(?:GPU\s*use|busy|utilization).*?([\d.]+)\s*%", line, re.IGNORECASE)
        if match:
            result["gpu_busy_pct"] = parse_float(match.group(1))
            continue

        # Power
        match = re.search(r"(?:power|Average Graphics).*?([\d.]+)\s*W", line, re.IGNORECASE)
        if match:
            result["gpu_power_w"] = parse_float(match.group(1))
            continue

    result["gpu_name"] = "AMD GPU (rocm-smi)"
    return result


def _collect_gpu_sysfs() -> dict[str, Any]:
    """Collect GPU metrics from sysfs (/sys/class/drm/card*).

    Returns a dict with gpu_name, gpu_busy_pct, gpu_temp_c.
    Power and VRAM are not available via sysfs on most AMD GPUs.
    """
    import glob

    result: dict[str, Any] = {
        "gpu_name": None,
        "gpu_busy_pct": None,
        "gpu_temp_c": None,
        "gpu_power_w": None,
        "gpu_vram_used_pct": None,
    }

    cards = sorted(glob.glob("/sys/class/drm/card*"))
    if not cards:
        return result

    if len(cards) > 1:
        logger.warning(
            "Multiple GPU cards detected via sysfs (%d). v1 stores only the first.",
            len(cards),
        )

    card = cards[0]
    card_num = card.rstrip("/").rsplit("card", 1)[-1]

    # GPU name (from lspci of the render device, or just the card name)
    result["gpu_name"] = f"GPU {card_num}"

    # Busy percent
    busy_path = f"{card}/device/gpu_busy_percent"
    busy_str = read_sysfs_file(busy_path)
    if busy_str is not None:
        result["gpu_busy_pct"] = parse_float(busy_str)

    # Temperature: look for hwmon under the card's device
    hwmon_dirs = sorted(glob.glob(f"{card}/device/hwmon/hwmon*"))
    for hwmon in hwmon_dirs:
        temp_path = f"{hwmon}/temp1_input"
        temp_str = read_sysfs_file(temp_path)
        if temp_str:
            temp_millic = parse_int(temp_str)
            if temp_millic:
                result["gpu_temp_c"] = temp_millic / 1000.0
                break

    # Try renderD* device as fallback for name/temp
    render_cards = sorted(glob.glob(f"/sys/class/drm/renderD*"))
    for render in render_cards:
        try:
            name_path = f"{render}/device/name"
            content = read_sysfs_file(name_path)
            if content:
                result["gpu_name"] = content.strip()
                break
        except Exception:
            pass

    return result


def _safe_float(value: Any) -> float | None:
    """Convert a value to float, returning None for unsupported markers."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    for marker in ROCM_UNSUPPORTED_MARKERS:
        if marker.lower() in s.lower():
            return None
    return parse_float(s)


def _compute_vram_pct(used: Any, total: Any) -> float | None:
    """Compute VRAM used percentage from used/total byte values."""
    used_f = _safe_float(used)
    total_f = _safe_float(total)
    if used_f is None or total_f is None or total_f == 0:
        return None
    return (used_f / total_f) * 100.0
