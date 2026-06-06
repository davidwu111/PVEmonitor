"""Shared API dependencies.

Provides get_db() and get_api_config() without circular imports.
Both api.py and api_routes/*.py import from here.
"""

from __future__ import annotations

from typing import Any

from .config import Config, get_config
from .db import get_readonly_connection

_config: Config | None = None


def get_api_config() -> Config:
    """Get or create the global API Config instance."""
    global _config
    if _config is None:
        _config = get_config()
    return _config


def get_db() -> Any:
    """Get a read-only database connection for the API."""
    cfg = get_api_config()
    return get_readonly_connection(str(cfg.db_path))
