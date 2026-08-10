"""FastAPI application for PVEmonitor.

Serves the dashboard and REST API endpoints on 0.0.0.0:8806, and runs the
collection loop in a background thread so all telemetry stays in memory.
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Config, get_config
from .collector import run_collection_loop
from .deps import set_storage
from .api_routes.health import router as health_router
from .api_routes.host import router as host_router
from .api_routes.guests import router as guests_router
from .storage import Storage
from .util import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    cfg = app.state.config
    storage = app.state.storage

    # Route collector/API logs to the configured rotating file.
    setup_logging(
        str(cfg.log_path),
        level=cfg.log_level,
        max_bytes=cfg.log_max_bytes,
        backup_count=cfg.log_backup_count,
    )

    # Restore the newest snapshot (if any), then import the legacy DB once.
    try:
        storage.load_latest_snapshot()
    except Exception as exc:
        logger.warning("Snapshot load failed: %s", exc)
    try:
        storage.import_legacy_db()
    except Exception as exc:
        logger.warning("Legacy DB import failed: %s", exc)

    # Start the background collection loop.
    stop_event = threading.Event()
    thread = threading.Thread(
        target=run_collection_loop,
        args=(storage, cfg, stop_event),
        name="pvemonitor-collector",
        daemon=True,
    )
    thread.start()

    logger.info("API server starting on %s:%d", cfg.api_host, cfg.api_port)
    try:
        yield
    finally:
        stop_event.set()
        thread.join(timeout=10)
        # Final snapshot so the newest data survives the shutdown.
        try:
            storage.snapshot()
        except Exception as exc:
            logger.warning("Shutdown snapshot failed: %s", exc)
        logger.info("API server shutting down")


def create_app(
    config: Config | None = None,
    storage: Storage | None = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        config: Resolved configuration (created from defaults if None).
        storage: Shared in-memory store (created if None). Useful in tests.
    """
    global _config
    if config is not None:
        _config = config
    else:
        _config = get_config()

    if storage is None:
        storage = Storage(_config)
    set_storage(storage)

    app = FastAPI(
        title="PVEmonitor",
        version=_config.collector_version,
        lifespan=lifespan,
    )
    app.state.config = _config
    app.state.storage = storage

    static_dir = Path(__file__).parent / "static"

    # Auth middleware: check token if configured.
    # Keep the dashboard and static assets reachable so the user can enter the
    # token client-side, and allow OPTIONS preflight requests.
    auth_token = _config.api_auth_token
    if auth_token:
        @app.middleware("http")
        async def auth_middleware(request: Request, call_next):
            path = request.url.path
            if request.method == "OPTIONS" or path in {"/", "/dashboard", "/api/health"} or path.startswith("/static/"):
                return await call_next(request)

            token = request.query_params.get("token") or request.headers.get("Authorization", "").removeprefix("Bearer ")
            if token != auth_token:
                return JSONResponse(
                    status_code=401,
                    content={"error": "Unauthorized", "detail": "Valid token required"},
                )
            return await call_next(request)

    # CORS: allow any LAN origin (dashboard may be loaded from any browser)
    @app.middleware("http")
    async def cors_middleware(request: Request, call_next):
        response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        return response

    # Redirect root to dashboard
    @app.get("/")
    async def root():
        return RedirectResponse(url="/dashboard")

    # Dashboard static file and locally served frontend assets.
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

        @app.get("/dashboard")
        async def dashboard():
            return FileResponse(static_dir / "dashboard.html")

    # Include API routers
    app.include_router(health_router)
    app.include_router(host_router)
    app.include_router(guests_router)

    return app
