"""Chat passthrough.

The REAL conversational loop runs through an external MCP client + the MCP server (``/mcp``):
the operator's assistant calls the Managarr MCP tools directly. This router is a thin,
LLM-optional fallback so the in-app chat box is functional without external LLM
credentials. With no LLM key configured it:
  - echoes the message,
  - heuristically names the MCP tool that would handle it, and
  - runs the matched READ-ONLY tool (library_status / health / list_missing /
    list_watchlist) inline so the user still gets useful data.

It never auto-invokes write tools (grab / queue_transcode) — those require the
MCP client path with confirmation.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth import require_user
from ..mcp import server as mcp

router = APIRouter()

# read-only tools we're willing to run directly from the fallback chat
_READONLY = {"library_status", "health", "list_missing", "list_watchlist",
             "search_releases", "run_scan"}

# crude keyword -> tool routing for the no-LLM fallback
_ROUTES = [
    (("missing", "wanted", "don't have"), "list_missing"),
    (("watchlist", "plex list"), "list_watchlist"),
    (("health", "status", "vpn", "leak", "disk", "space"), "health"),
    (("how many", "library", "count", "total"), "library_status"),
    (("search", "find", "look for"), "search_releases"),
    (("scan", "rescan"), "run_scan"),
    (("grab", "download", "get me", "fetch"), "grab"),
    (("transcode", "convert", "hevc", "x265"), "queue_transcode"),
]


@router.get("/tools")
def list_tools():
    """List the MCP tool schemas (same set exposed at /mcp tools/list)."""
    return {"tools": mcp.tool_schemas()}


def _route(message: str) -> str:
    m = (message or "").lower()
    for keywords, tool in _ROUTES:
        if any(k in m for k in keywords):
            return tool
    return "library_status"


class ChatBody(BaseModel):
    message: str


@router.post("")
@router.post("/")
def chat(body: ChatBody, user: str = Depends(require_user)):
    message = body.message or ""
    tool = _route(message)
    tool_meta = mcp.TOOLS.get(tool, {})
    is_write = tool_meta.get("write", False)

    result = None
    note = (
        "No conversational LLM is configured in-app. The real chat runs through "
        "an MCP client connected to the Managarr MCP server (/mcp). "
    )
    if tool in _READONLY and not is_write:
        try:
            # build minimal args for tools that need them
            args = {}
            if tool == "search_releases":
                args = {"media_kind": "movie", "query": message}
            result = tool_meta["handler"](args)
            note += f"Ran read-only tool '{tool}' for you."
        except Exception as exc:
            note += f"Tried '{tool}' but it errored: {exc}"
    else:
        note += (f"Your request maps to the WRITE tool '{tool}', which I won't run "
                 "from this fallback chat — invoke it via an MCP client (with confirmation).")

    return {
        "echo": message,
        "suggested_tool": tool,
        "ran": result is not None,
        "result": result,
        "note": note,
    }
