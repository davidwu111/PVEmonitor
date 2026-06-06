"""Configuration loader for PVEmonitor.

Reads monitor.yaml and resolves all paths relative to PVEMONITOR_HOME.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any

import yaml


# Default config values (used when monitor.yaml is missing or keys are absent)
DEFAULTS: dict[str, Any] = {
    "monitor": {
        "sample_interval_s": 10,
        "hostname_override": None,
        "collection_timeout_s": 25,
    },
    "paths": {
        "db": "runtime/db/metrics.sqlite3",
        "log": "runtime/logs/collector.log",
        "lock": "runtime/locks/collector.lock",
    },
    "collection": {
        "host_enabled": True,
        "guests_enabled": True,
        "gpu_enabled": True,
        "psi_enabled": True,
        "top_process_enabled": True,
    },
    "gpu": {
        "rocm_smi_bin": "/usr/bin/rocm-smi",
        "nvidia_smi_bin": None,  # auto-detect from PATH if None
        "sysfs_fallback": True,
    },
    "rate_calculation": {
        "max_interval_s": 120,
    },
    "logging": {
        "level": "INFO",
        "max_log_bytes": 10_485_760,
        "backup_count": 5,
    },
    "api": {
        "host": "0.0.0.0",
        "port": 8806,
        "auth_token": "",
    },
    "locking": {
        "stale_timeout_multiplier": 5,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _resolve_home() -> Path:
    """Determine PVEMONITOR_HOME from environment or current working directory."""
    env = os.environ.get("PVEMONITOR_HOME")
    if env:
        return Path(env)
    # Fallback: assume we're running from the project root
    # (the directory containing pyproject.toml, config/, src/, etc.)
    cwd = Path.cwd()
    markers = ["pyproject.toml", "config/monitor.yaml", "src/pvemonitor"]
    for marker in markers:
        if (cwd / marker).exists():
            return cwd
    # Walk upward to find project root
    for parent in cwd.parents:
        for marker in markers:
            if (parent / marker).exists():
                return parent
    return cwd


class Config:
    """Resolved PVEmonitor configuration.

    All path values are resolved to absolute paths.
    """

    def __init__(self, config_path: str | Path | None = None):
        self._home = _resolve_home()

        # Load YAML
        if config_path:
            yaml_path = Path(config_path)
        else:
            yaml_path = self._home / "config" / "monitor.yaml"

        raw: dict[str, Any] = {}
        if yaml_path.exists():
            with open(yaml_path) as f:
                raw = yaml.safe_load(f) or {}

        # Merge with defaults
        merged = _deep_merge(DEFAULTS, raw)
        self._data = merged

        # Resolve paths
        self.db_path = self._resolve_path(merged["paths"]["db"])
        self.log_path = self._resolve_path(merged["paths"]["log"])
        self.lock_path = self._resolve_path(merged["paths"]["lock"])

        # Ensure runtime directories exist
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, rel_path: str) -> Path:
        """Resolve a config path (absolute or relative to PVEMONITOR_HOME)."""
        p = Path(rel_path)
        if p.is_absolute():
            return p
        return (self._home / p).resolve()

    @property
    def home(self) -> Path:
        return self._home

    @property
    def sample_interval_s(self) -> int:
        return self._data["monitor"]["sample_interval_s"]

    @property
    def hostname(self) -> str:
        override = self._data["monitor"]["hostname_override"]
        if override:
            return override
        return socket.gethostname()

    @property
    def collection_timeout_s(self) -> int:
        return self._data["monitor"]["collection_timeout_s"]

    @property
    def host_enabled(self) -> bool:
        return self._data["collection"]["host_enabled"]

    @property
    def guests_enabled(self) -> bool:
        return self._data["collection"]["guests_enabled"]

    @property
    def gpu_enabled(self) -> bool:
        return self._data["collection"]["gpu_enabled"]

    @property
    def psi_enabled(self) -> bool:
        return self._data["collection"]["psi_enabled"]

    @property
    def top_process_enabled(self) -> bool:
        return self._data["collection"]["top_process_enabled"]

    @property
    def rocm_smi_bin(self) -> str:
        return self._data["gpu"]["rocm_smi_bin"]

    @property
    def nvidia_smi_bin(self) -> str | None:
        return self._data["gpu"]["nvidia_smi_bin"]

    @property
    def gpu_sysfs_fallback(self) -> bool:
        return self._data["gpu"]["sysfs_fallback"]

    @property
    def max_rate_interval_s(self) -> int:
        return self._data["rate_calculation"]["max_interval_s"]

    @property
    def log_level(self) -> str:
        return self._data["logging"]["level"]

    @property
    def log_max_bytes(self) -> int:
        return self._data["logging"]["max_log_bytes"]

    @property
    def log_backup_count(self) -> int:
        return self._data["logging"]["backup_count"]

    @property
    def api_host(self) -> str:
        return self._data["api"]["host"]

    @property
    def api_port(self) -> int:
        return self._data["api"]["port"]

    @property
    def api_auth_token(self) -> str:
        return self._data["api"]["auth_token"]

    @property
    def stale_lock_timeout_s(self) -> int:
        return (
            self._data["locking"]["stale_timeout_multiplier"]
            * self.sample_interval_s
        )

    @property
    def collector_version(self) -> str:
        try:
            from importlib.metadata import version
            return version("pvemonitor")
        except Exception:
            return "0.1.0"

    def as_dict(self) -> dict:
        """Return resolved config for display (paths resolved)."""
        return {
            "home": str(self.home),
            "hostname": self.hostname,
            "sample_interval_s": self.sample_interval_s,
            "collection_timeout_s": self.collection_timeout_s,
            "db_path": str(self.db_path),
            "log_path": str(self.log_path),
            "lock_path": str(self.lock_path),
            "host_enabled": self.host_enabled,
            "guests_enabled": self.guests_enabled,
            "gpu_enabled": self.gpu_enabled,
            "psi_enabled": self.psi_enabled,
            "top_process_enabled": self.top_process_enabled,
            "rocm_smi_bin": self.rocm_smi_bin,
            "nvidia_smi_bin": self.nvidia_smi_bin or "(auto-detect)",
            "gpu_sysfs_fallback": self.gpu_sysfs_fallback,
            "max_rate_interval_s": self.max_rate_interval_s,
            "log_level": self.log_level,
            "api_host": self.api_host,
            "api_port": self.api_port,
            "api_auth_enabled": bool(self.api_auth_token),
            "stale_lock_timeout_s": self.stale_lock_timeout_s,
            "collector_version": self.collector_version,
        }


# Module-level singleton, initialized on first use
_config: Config | None = None


def get_config(config_path: str | Path | None = None) -> Config:
    """Get or create the global Config instance."""
    global _config
    if _config is None or config_path is not None:
        _config = Config(config_path)
    return _config
