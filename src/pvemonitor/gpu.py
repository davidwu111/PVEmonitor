"""GPU metrics collection for PVEmonitor.

Multi-vendor support:
    AMD    — rocm-smi (primary: JSON, fallback: text parsing)
    NVIDIA — nvidia-smi (CSV query)
    Other  — sysfs (/sys/class/drm/card*/) for basic busy% + temperature

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

# Fields reported by vendor tools that may be unsupported (store NULL)
UNSUPPORTED_MARKERS = ["N/A", "unsupported", "Unknown error", "[Not Supported]"]

# Known nvidia-smi binary locations
_NVIDIA_SMI_CANDIDATES = [
    "/usr/bin/nvidia-smi",
    "/usr/local/bin/nvidia-smi",
    "nvidia-smi",  # PATH lookup
]


def collect_gpu_metrics(config: Config) -> dict[str, Any]:
    """Collect GPU metrics. Tries vendor tools in order, falls back to sysfs.

    Detection order:
        1. rocm-smi       (AMD GPUs)
        2. nvidia-smi     (NVIDIA GPUs)
        3. sysfs          (any GPU with standard DRM interfaces)

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

    # 1. Try AMD rocm-smi
    try:
        metrics = _collect_rocm_smi(config.rocm_smi_bin)
        if metrics and any(v is not None for v in metrics.values()):
            result.update(metrics)
            logger.debug("GPU metrics collected via rocm-smi (AMD)")
            return result
    except SubprocessError:
        logger.debug("rocm-smi not available")

    # 2. Try NVIDIA nvidia-smi
    try:
        metrics = _collect_nvidia_smi(config.nvidia_smi_bin)
        if metrics and any(v is not None for v in metrics.values()):
            result.update(metrics)
            logger.debug("GPU metrics collected via nvidia-smi (NVIDIA)")
            return result
    except SubprocessError:
        logger.debug("nvidia-smi not available")

    # 3. Sysfs fallback (AMD iGPU, Intel Arc, or any DRM device)
    if config.gpu_sysfs_fallback:
        try:
            metrics = _collect_gpu_sysfs()
            if metrics and any(v is not None for v in metrics.values()):
                result.update(metrics)
                logger.debug("GPU metrics collected via sysfs fallback")
        except Exception as exc:
            logger.warning("GPU sysfs collection failed: %s", exc)

    return result


# ─── AMD: rocm-smi ──────────────────────────────────────────────────────

def _collect_rocm_smi(bin_path: str) -> dict[str, Any]:
    """Collect GPU metrics from rocm-smi (AMD)."""
    result: dict[str, Any] = {
        "gpu_name": None, "gpu_busy_pct": None,
        "gpu_temp_c": None, "gpu_power_w": None, "gpu_vram_used_pct": None,
    }

    import json

    # Attempt JSON output
    try:
        stdout = run_cmd(
            [bin_path, "--showuse", "--showtemp", "--showpower",
             "--showmeminfo", "vram", "--json"],
            timeout=5.0,
        )
        if stdout:
            data = json.loads(stdout)
            return _parse_rocm_json(data)
    except (SubprocessError, json.JSONDecodeError):
        pass

    # Fallback: text-mode parsing
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
    """Parse rocm-smi JSON output."""
    result: dict[str, Any] = {
        "gpu_name": None, "gpu_busy_pct": None,
        "gpu_temp_c": None, "gpu_power_w": None, "gpu_vram_used_pct": None,
    }

    cards: list[dict] = []
    if isinstance(data, dict):
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
            for k, v in data.items():
                if isinstance(v, dict) and "GPU" in k:
                    cards.append(v)
            if not cards and data:
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
        use = card.get("GPU use (%)", card.get("GPU utilization (%)", card.get("busy_percent")))
        result["gpu_busy_pct"] = _safe_float(use)
        temp = card.get("Temperature (Sensor edge) (C)", card.get("Temperature (C)", card.get("temp")))
        result["gpu_temp_c"] = _safe_float(temp)
        power = card.get("Average Graphics Package Power (W)", card.get("Power (W)", card.get("power")))
        result["gpu_power_w"] = _safe_float(power)
        vram_pct = card.get("VRAM (%)", card.get("vram_percent", card.get("vram_used_pct")))
        if vram_pct is None:
            used = card.get("VRAM Total Used Memory (B)", card.get("vram_used_bytes"))
            total = card.get("VRAM Total Memory (B)", card.get("vram_total_bytes"))
            vram_pct = _compute_vram_pct(used, total)
        result["gpu_vram_used_pct"] = _safe_float(vram_pct)

    return result


def _parse_rocm_text(stdout: str) -> dict[str, Any]:
    """Parse text-mode rocm-smi output (non-JSON fallback)."""
    result: dict[str, Any] = {
        "gpu_name": None, "gpu_busy_pct": None,
        "gpu_temp_c": None, "gpu_power_w": None, "gpu_vram_used_pct": None,
    }

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.search(r"temperature.*?([\d.]+)\s*C", line, re.IGNORECASE)
        if match:
            result["gpu_temp_c"] = parse_float(match.group(1))
            continue
        match = re.search(r"(?:GPU\s*use|busy|utilization).*?([\d.]+)\s*%", line, re.IGNORECASE)
        if match:
            result["gpu_busy_pct"] = parse_float(match.group(1))
            continue
        match = re.search(r"(?:power|Average Graphics).*?([\d.]+)\s*W", line, re.IGNORECASE)
        if match:
            result["gpu_power_w"] = parse_float(match.group(1))
            continue

    result["gpu_name"] = "AMD GPU (rocm-smi)"
    return result


# ─── NVIDIA: nvidia-smi ─────────────────────────────────────────────────

def _find_nvidia_smi(preferred: str | None = None) -> str | None:
    """Locate the nvidia-smi binary.

    Checks the configured path first, then common install locations.
    Returns the path if found, or None.
    """
    import os

    candidates = []
    if preferred:
        candidates.append(preferred)
    candidates.extend(_NVIDIA_SMI_CANDIDATES)

    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
        # PATH lookup for bare command names
        if "/" not in candidate:
            import shutil
            found = shutil.which(candidate)
            if found:
                return found
    return None


def _collect_nvidia_smi(preferred_bin: str | None = None) -> dict[str, Any]:
    """Collect GPU metrics from nvidia-smi (NVIDIA).

    Uses the query API for structured CSV output:
        nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu,
                     power.draw,memory.used,memory.total
                   --format=csv,noheader,nounits

    Returns a dict with all five GPU host_metrics columns.
    """
    bin_path = _find_nvidia_smi(preferred_bin)
    if bin_path is None:
        raise SubprocessError("nvidia-smi not found")

    stdout = run_cmd(
        [
            bin_path,
            "--query-gpu=name,temperature.gpu,utilization.gpu,power.draw,memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ],
        timeout=5.0,
    )

    if not stdout:
        raise SubprocessError("nvidia-smi returned empty output")

    return _parse_nvidia_csv(stdout)


def _parse_nvidia_csv(stdout: str) -> dict[str, Any]:
    """Parse nvidia-smi CSV output into host_metrics GPU fields.

    Expected format (one line per GPU):
        GPU Name, 65, 87, 320.50, 16384, 24576

    v1: stores only the first GPU; logs a warning if multiple GPUs are found.
    """
    result: dict[str, Any] = {
        "gpu_name": None, "gpu_busy_pct": None,
        "gpu_temp_c": None, "gpu_power_w": None, "gpu_vram_used_pct": None,
    }

    lines = [l.strip() for l in stdout.splitlines() if l.strip()]
    if not lines:
        return result

    if len(lines) > 1:
        logger.warning(
            "Multiple GPUs detected via nvidia-smi (%d). v1 stores only the first GPU's metrics.",
            len(lines),
        )

    # Parse first GPU
    parts = [p.strip() for p in lines[0].split(",")]
    if len(parts) < 6:
        logger.warning("Unexpected nvidia-smi CSV format: %s", lines[0])
        return result

    result["gpu_name"] = parts[0] if parts[0] else None
    result["gpu_temp_c"] = _safe_float(parts[1])
    result["gpu_busy_pct"] = _safe_float(parts[2])
    result["gpu_power_w"] = _safe_float(parts[3])

    # VRAM: compute used percentage from used/total
    vram_used = _safe_float(parts[4])
    vram_total = _safe_float(parts[5])
    result["gpu_vram_used_pct"] = _compute_vram_pct(vram_used, vram_total)

    return result


# ─── Sysfs fallback (any GPU with standard DRM interfaces) ─────────────

def _collect_gpu_sysfs() -> dict[str, Any]:
    """Collect GPU metrics from sysfs (/sys/class/drm/card*).

    Works with AMD iGPUs, Intel Arc, and any GPU that exposes
    gpu_busy_percent and hwmon temperature via the DRM subsystem.

    Returns busy%, temperature, and name. Power and VRAM are not
    available via this path.
    """
    import glob

    result: dict[str, Any] = {
        "gpu_name": None, "gpu_busy_pct": None,
        "gpu_temp_c": None, "gpu_power_w": None, "gpu_vram_used_pct": None,
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

    result["gpu_name"] = f"GPU {card_num}"

    # Busy percent
    busy_path = f"{card}/device/gpu_busy_percent"
    busy_str = read_sysfs_file(busy_path)
    if busy_str is not None:
        result["gpu_busy_pct"] = parse_float(busy_str)

    # Temperature
    hwmon_dirs = sorted(glob.glob(f"{card}/device/hwmon/hwmon*"))
    for hwmon in hwmon_dirs:
        temp_path = f"{hwmon}/temp1_input"
        temp_str = read_sysfs_file(temp_path)
        if temp_str:
            temp_millic = parse_int(temp_str)
            if temp_millic:
                result["gpu_temp_c"] = temp_millic / 1000.0
                break

    # Try to read a human-readable device name
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


# ─── Shared helpers ─────────────────────────────────────────────────────

def _safe_float(value: Any) -> float | None:
    """Convert a value to float, returning None for unsupported markers."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    for marker in UNSUPPORTED_MARKERS:
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
