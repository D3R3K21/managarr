"""Managarr MCP server (hand-rolled JSON-RPC over HTTP, no ``mcp`` pip dep).

``mount_mcp(app)`` adds an MCP-compatible endpoint under ``/mcp``:
  - POST /mcp  — JSON-RPC 2.0: ``initialize``, ``tools/list``, ``tools/call``.
  - GET  /mcp  — minimal SSE stub (some clients probe this; we just keep it open
                 briefly / return an endpoint event).

CLIENT WIRING
  Register this server with any MCP client (e.g. Claude Code) as an HTTP MCP
  endpoint (POST {"jsonrpc":"2.0",...} to http://<managarr>/mcp).
  The assistant can then call library_status / search_releases / grab / list_missing /
  list_watchlist / queue_transcode / run_scan / health as tools.

SAFETY
  ``grab`` and ``queue_transcode`` are *write* tools (they fetch torrents / replace
  files). MCP clients should treat these as confirm-required — do NOT auto-approve them.
  Read tools (library_status, list_*, health, run_scan-readonly) are safe.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..db import session_scope
from ..pipeline import orchestrator
from ..services import collections as collections_svc
from ..services import health as health_svc
from ..services import transcode as transcode_svc
from ..services import watchlist as watchlist_svc

log = logging.getLogger("managarr.mcp")

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "managarr", "version": "0.1.0"}


# ── tool handlers (each returns JSON-serializable data) ───────────────────────
def _t_library_status(args: dict) -> dict:
    from sqlmodel import select, func
    from ..models import Audiobook, Episode, Movie, Series
    with session_scope() as s:
        movies = s.exec(select(func.count()).select_from(Movie)).one()
        movies_have = s.exec(select(func.count()).select_from(Movie).where(Movie.has_file == True)).one()  # noqa: E712
        series = s.exec(select(func.count()).select_from(Series)).one()
        episodes = s.exec(select(func.count()).select_from(Episode)).one()
        episodes_have = s.exec(select(func.count()).select_from(Episode).where(Episode.has_file == True)).one()  # noqa: E712
        books = s.exec(select(func.count()).select_from(Audiobook)).one()
    return {
        "movies": {"total": movies, "with_file": movies_have, "missing": movies - movies_have},
        "tv": {"series": series, "episodes": episodes, "episodes_with_file": episodes_have},
        "audiobooks": {"total": books},
    }


def _t_search_releases(args: dict) -> dict:
    kind = args.get("media_kind", "movie")
    query = args.get("query", "")
    author = args.get("author", "") or ""
    releases = orchestrator.search(kind, query, author=author)
    # trim payload for the model
    return {"count": len(releases),
            "releases": [{k: r.get(k) for k in ("title", "indexer", "size_bytes",
                                                 "seeders", "leechers", "infohash")}
                         for r in releases[:25]]}


def _t_grab(args: dict) -> dict:
    kind = args.get("media_kind", "movie")
    query = args.get("query", "")
    author = args.get("author", "") or ""
    if not query:
        return {"error": "query is required"}
    # build a sensible save_path from the query
    save_path = orchestrator.build_save_path(
        kind if kind in ("movie", "tv", "audiobook") else "movie",
        title=query, author=author)
    grab = orchestrator.auto_grab(kind, query, save_path, author=author, source="manual")
    if not grab:
        return {"grabbed": False, "reason": "no release passed the quality profile"}
    return {"grabbed": True, "title": grab.title, "indexer": grab.indexer,
            "save_path": grab.save_path, "grab_id": grab.id}


def _t_list_missing(args: dict) -> dict:
    kind = args.get("kind")
    with session_scope() as s:
        rows = collections_svc.missing(s, kind=kind)
        return {"count": len(rows),
                "missing": [{"id": r.id, "title": r.title, "item_kind": r.item_kind,
                             "year": r.year} for r in rows[:100]]}


def _t_list_watchlist(args: dict) -> dict:
    with session_scope() as s:
        return watchlist_svc.list_watchlist(s)


def _t_queue_transcode(args: dict) -> dict:
    path = args.get("path")
    with session_scope() as s:
        if path:
            from ..models import TranscodeJob
            job = TranscodeJob(media_kind="movie", source_path=path, status="candidate",
                               reason="queued via MCP")
            s.add(job)
            s.commit()
            s.refresh(job)
            return {"queued": True, "job_id": job.id, "path": path}
        return transcode_svc.scan_candidates(s)


def _t_run_scan(args: dict) -> dict:
    from ..pipeline.scanner import scan_all, scan_audiobooks, scan_movies, scan_tv
    kind = args.get("kind")
    with session_scope() as s:
        if kind == "movie":
            return scan_movies(s)
        if kind in ("tv", "series"):
            return scan_tv(s)
        if kind == "audiobook":
            return scan_audiobooks(s)
        return scan_all(s)


def _t_health(args: dict) -> dict:
    with session_scope() as s:
        return {"checks": health_svc.checks(s)}


# ── tool registry (name -> (schema, handler, write?)) ─────────────────────────
TOOLS: dict[str, dict[str, Any]] = {
    "library_status": {
        "description": "Counts of movies/TV/audiobooks and how many are missing files.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _t_library_status, "write": False,
    },
    "search_releases": {
        "description": "Search indexers for releases. Read-only (does not grab).",
        "inputSchema": {"type": "object", "properties": {
            "media_kind": {"type": "string", "enum": ["movie", "tv", "audiobook"]},
            "query": {"type": "string"},
            "author": {"type": "string"},
        }, "required": ["media_kind", "query"]},
        "handler": _t_search_releases, "write": False,
    },
    "grab": {
        "description": "Search + auto-grab the best release (WRITE: downloads a torrent).",
        "inputSchema": {"type": "object", "properties": {
            "media_kind": {"type": "string", "enum": ["movie", "tv", "audiobook"]},
            "query": {"type": "string"},
            "author": {"type": "string"},
        }, "required": ["media_kind", "query"]},
        "handler": _t_grab, "write": True,
    },
    "list_missing": {
        "description": "List collection members not yet owned (optionally by kind).",
        "inputSchema": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["movie", "audiobook"]},
        }},
        "handler": _t_list_missing, "write": False,
    },
    "list_watchlist": {
        "description": "Cached view of the last Plex watchlist sync.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _t_list_watchlist, "write": False,
    },
    "queue_transcode": {
        "description": "Queue a transcode candidate (path) or scan the library (WRITE).",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
        "handler": _t_queue_transcode, "write": True,
    },
    "run_scan": {
        "description": "Rescan the library from disk (optionally one kind).",
        "inputSchema": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["movie", "tv", "audiobook"]},
        }},
        "handler": _t_run_scan, "write": False,
    },
    "health": {
        "description": "Run system health checks (qBit, VPN guard, Plex, disk, DB).",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _t_health, "write": False,
    },
}


def tool_schemas() -> list[dict]:
    """Public list of tool schemas (used by /api/chat/tools too)."""
    return [
        {"name": name, "description": t["description"], "inputSchema": t["inputSchema"],
         "write": t["write"]}
        for name, t in TOOLS.items()
    ]


# ── JSON-RPC handling ─────────────────────────────────────────────────────────
def _rpc_result(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _handle_rpc(payload: dict) -> dict:
    method = payload.get("method")
    req_id = payload.get("id")
    params = payload.get("params") or {}

    if method == "initialize":
        return _rpc_result(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        })

    if method in ("notifications/initialized", "initialized"):
        return None  # notification: no response

    if method == "tools/list":
        return _rpc_result(req_id, {
            "tools": [{"name": n, "description": t["description"],
                       "inputSchema": t["inputSchema"]} for n, t in TOOLS.items()],
        })

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = TOOLS.get(name)
        if not tool:
            return _rpc_error(req_id, -32602, f"unknown tool {name!r}")
        try:
            data = tool["handler"](args)
        except Exception as exc:
            log.exception("mcp tool %s failed", name)
            return _rpc_result(req_id, {
                "content": [{"type": "text", "text": f"error: {exc}"}],
                "isError": True,
            })
        import json
        return _rpc_result(req_id, {
            "content": [{"type": "text", "text": json.dumps(data, default=str)}],
            "isError": False,
        })

    return _rpc_error(req_id, -32601, f"method not found: {method}")


def mount_mcp(app: FastAPI) -> None:
    @app.post("/mcp")
    async def mcp_post(request: Request):  # noqa: ANN001
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(_rpc_error(None, -32700, "parse error"), status_code=400)
        # support a batch array too
        if isinstance(payload, list):
            out = [r for r in (_handle_rpc(p) for p in payload) if r is not None]
            return JSONResponse(out)
        resp = _handle_rpc(payload)
        if resp is None:
            return JSONResponse({}, status_code=202)
        return JSONResponse(resp)

    @app.get("/mcp")
    async def mcp_get():  # SSE stub for clients that probe the GET transport
        async def _gen():
            yield "event: endpoint\ndata: /mcp\n\n"
        return StreamingResponse(_gen(), media_type="text/event-stream")

    log.info("MCP server mounted at /mcp (%d tools)", len(TOOLS))
