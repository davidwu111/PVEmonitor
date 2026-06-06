"""Utility helpers: subprocess wrappers, parsing, NULL handling."""

from __future__ import annotations

import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

class SubprocessError(Exception):
    """Raised when a subprocess call fails (non-zero exit, timeout, etc.)."""

    def __init__(self, message: str, scope: str = "", vmid: int | None = None):
        super().__init__(message)
        self.scope = scope
        self.vmid = vmid


def run_cmd(
    args: list[str],
    timeout: float = 10.0,
    capture_stderr: bool = True,
) -> str:
    """Run a command and return its stripped stdout.

    Raises SubprocessError on non-zero exit, timeout, or other OSError.
    """
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise SubprocessError(f"Command timed out after {timeout}s: {' '.join(args)}")
    except FileNotFoundError:
        raise SubprocessError(f"Command not found: {args[0]}")
    except OSError as exc:
        raise SubprocessError(f"OS error running {' '.join(args)}: {exc}")

    if result.returncode != 0:
        stderr_info = ""
        if capture_stderr and result.stderr:
            stderr_info = f" — stderr: {result.stderr.strip()[:200]}"
        raise SubprocessError(
            f"Command exited {result.returncode}: {' '.join(args)}{stderr_info}"
        )

    return result.stdout.strip()


def run_json_cmd(
    args: list[str],
    timeout: float = 10.0,
) -> dict[str, Any] | list[Any]:
    """Run a command that emits JSON on stdout. Returns parsed JSON."""
    import json

    stdout = run_cmd(args, timeout=timeout)
    if not stdout:
        raise SubprocessError(f"Empty output from {' '.join(args)}")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise SubprocessError(f"Invalid JSON from {' '.join(args)}: {exc}")


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_float(value: str | None) -> float | None:
    """Parse a string to float, returning None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def parse_int(value: str | None) -> int | None:
    """Parse a string to int, returning None on failure."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def safe_get(dct: dict, *keys: str, default=None):
    """Safely traverse nested dict keys, returning default if any key is missing."""
    current = dct
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def null_if_none(value: Any) -> Any:
    """Identity function — primarily a semantic marker for NULL intent."""
    return value


# ---------------------------------------------------------------------------
# File parsing helpers
# ---------------------------------------------------------------------------

def read_proc_file(path: str) -> str | None:
    """Read a /proc file, returning None if it doesn't exist."""
    try:
        with open(path) as f:
            return f.read().strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def read_sysfs_file(path: str) -> str | None:
    """Read a /sys file, returning None if it doesn't exist."""
    return read_proc_file(path)


# ---------------------------------------------------------------------------
# PSI parsing
# ---------------------------------------------------------------------------

def parse_psi_file(path: str) -> dict[str, float | None]:
    """Parse a /proc/pressure/* file and return a dict of avg10/avg60/avg300.

    Example /proc/pressure/cpu content:
        some avg10=0.00 avg60=0.01 avg300=0.00 total=12345
        full avg10=0.00 avg60=0.00 avg300=0.00 total=6789

    Returns:
        {"some_avg10": 0.0, "some_avg60": 0.01, "some_avg300": 0.0,
         "full_avg10": 0.0, "full_avg60": 0.0, "full_avg300": 0.0}
    """
    result: dict[str, float | None] = {
        "some_avg10": None, "some_avg60": None, "some_avg300": None,
        "full_avg10": None, "full_avg60": None, "full_avg300": None,
    }
    content = read_proc_file(path)
    if not content:
        return result

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 1:
            continue
        line_type = parts[0]  # "some" or "full"
        for token in parts[1:]:
            if "=" in token:
                key, val = token.split("=", 1)
                field = f"{line_type}_{key}"
                if field in result:
                    result[field] = parse_float(val)
    return result


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(
    log_path: str,
    level: str = "INFO",
    max_bytes: int = 10_485_760,
    backup_count: int = 5,
) -> logging.Logger:
    """Configure the root logger with rotating file handler + stderr."""
    from logging.handlers import RotatingFileHandler

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Avoid duplicate handlers
    if root.handlers:
        return logging.getLogger("pvemonitor")

    # Console handler
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.addHandler(console)

    # Rotating file handler
    try:
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
        )
        file_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        ))
        root.addHandler(file_handler)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        logging.warning("Could not create log file at %s: %s", log_path, exc)

    return logging.getLogger("pvemonitor")
