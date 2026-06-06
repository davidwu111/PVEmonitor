"""FastAPI application for PVEmonitor.

Serves the dashboard and REST API endpoints on 0.0.0.0:8806.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Config, get_config
from .deps import get_api_config, get_db
from .api_routes.health import router as health_router
from .api_routes.host import router as host_router
from .api_routes.guests import router as guests_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    cfg = get_api_config()
    logger.info("API server starting on %s:%d", cfg.api_host, cfg.api_port)
    yield
    # Shutdown
    logger.info("API server shutting down")


def create_app(config: Config | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    global _config
    if config is not None:
        _config = config
    else:
        _config = get_config()

    app = FastAPI(
        title="PVEmonitor",
        version=_config.collector_version,
        lifespan=lifespan,
    )

    # Auth middleware: check token if configured
    auth_token = _config.api_auth_token
    if auth_token:
        @app.middleware("http")
        async def auth_middleware(request: Request, call_next):
            # Skip auth for health endpoint
            if request.url.path == "/api/health":
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

    # Dashboard static file
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        @app.get("/dashboard")
        async def dashboard():
            return FileResponse(static_dir / "dashboard.html")

    # Include API routers
    app.include_router(health_router)
    app.include_router(host_router)
    app.include_router(guests_router)

    return app
