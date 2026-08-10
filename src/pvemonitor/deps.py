"""Shared API dependencies.

Provides get_db() and get_api_config() without circular imports.
get_db() returns a read handle to the shared in-memory telemetry store.
"""

from __future__ import annotations

from typing import Any

from .config import Config, get_config
from .storage import Storage

_storage: Storage | None = None


def set_storage(storage: Storage | None) -> None:
    """Set the shared telemetry store (used by create_app and tests)."""
    global _storage
    _storage = storage


def get_storage() -> Storage:
    """Get or create the shared telemetry store."""
    global _storage
    if _storage is None:
        _storage = Storage(get_config())
    return _storage


def get_api_config() -> Config:
    """Get the global API Config instance."""
    return get_config()


def get_db() -> Any:
    """Get a read handle for the in-memory telemetry store."""
    return get_storage().read_handle()
