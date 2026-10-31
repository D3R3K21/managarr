"""Managarr FastAPI application entrypoint."""
from __future__ import annotations

import importlib
import logging
import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
log = logging.getLogger("managarr")

# (module, url_prefix, tag). Each module must expose `router: APIRouter`.
ROUTER_SPECS = [
    ("app.auth", "/api/auth", "auth"),
    ("app.routers.users", "/api/users", "users"),
    ("app.routers.library", "/api/library", "library"),
    ("app.routers.scan", "/api/scan", "scan"),
    ("app.routers.collections", "/api/collections", "collections"),
    ("app.routers.search", "/api/search", "search"),
    ("app.routers.discover", "/api/discover", "discover"),
    ("app.routers.calendar", "/api/calendar", "calendar"),
    ("app.routers.indexers", "/api/indexers", "indexers"),
    ("app.routers.grabs", "/api/grabs", "grabs"),
    ("app.routers.quality", "/api/quality", "quality"),
    ("app.routers.watchlist", "/api/watchlist", "watchlist"),
    ("app.routers.transcode", "/api/transcode", "transcode"),
    ("app.routers.devices", "/api/devices", "devices"),
    ("app.routers.scripts", "/api/scripts", "scripts"),
    ("app.routers.health", "/api/health", "health"),
    ("app.routers.settings", "/api/settings", "settings"),
    ("app.routers.chat", "/api/chat", "chat"),
]


def _mount_routers(app: FastAPI) -> None:
    for module_path, prefix, tag in ROUTER_SPECS:
        try:
            mod = importlib.import_module(module_path)
            router: APIRouter = getattr(mod, "router")
            app.include_router(router, prefix=prefix, tags=[tag])
            log.info("mounted %s at %s", module_path, prefix)
        except Exception as exc:  # never let one router break the whole API
            log.error("failed to mount %s: %s", module_path, exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Seed defaults (indexers, quality profiles, root folders) if empty.
    try:
        from .bootstrap import seed_defaults

        seed_defaults()
    except Exception:
        log.exception("seed_defaults failed (non-fatal)")
    # Scheduler
    if settings.ENABLE_SCHEDULER:
        try:
            from .scheduler.runner import start_scheduler

            start_scheduler()
        except Exception:
            log.exception("scheduler failed to start (non-fatal)")
    # MCP server (mounted as sub-app if available)
    if settings.ENABLE_MCP:
        try:
            from .mcp.server import mount_mcp

            mount_mcp(app)
        except Exception:
            log.exception("MCP mount failed (non-fatal)")
    yield


app = FastAPI(title="Managarr", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_mount_routers(app)


@app.get("/api/ping")
def ping():
    return {"ok": True, "app": "managarr", "version": "0.1.0"}


# ── Serve the built frontend (SPA) ────────────────────────────────────────────
STATIC_DIR = os.getenv("STATIC_DIR", "/app/static")
if os.path.isdir(STATIC_DIR):
    assets = os.path.join(STATIC_DIR, "assets")
    if os.path.isdir(assets):
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    # The SPA shell must always revalidate: it references content-hashed asset
    # filenames, so a cached index.html keeps pointing at the OLD bundle after a
    # deploy. Without an explicit header browsers heuristically cache it and serve a
    # stale app. no-cache = store but revalidate (cheap 304 when unchanged).
    def _index(status_code: int = 200):
        return FileResponse(os.path.join(STATIC_DIR, "index.html"),
                            status_code=status_code, headers={"Cache-Control": "no-cache"})

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        # API 404s should not fall through to index.html.
        if full_path.startswith("api/"):
            return _index(404)
        candidate = os.path.join(STATIC_DIR, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return _index()
