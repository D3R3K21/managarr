"""ntfy notifications + unified history logging.

`notify()` posts to ntfy (best-effort, never raises into callers) and also
writes a HistoryEvent so the Activity page is the single source of truth.
"""
from __future__ import annotations

import logging
from typing import Optional

from .config import settings
from .db import session_scope
from .models import HistoryEvent

log = logging.getLogger("managarr.notify")


def log_event(kind: str, title: str = "", message: str = "", media_kind: Optional[str] = None, meta: Optional[dict] = None) -> None:
    try:
        with session_scope() as s:
            s.add(HistoryEvent(kind=kind, media_kind=media_kind, title=title or None, message=message or None, meta=meta))
    except Exception:
        log.exception("failed to write history event")


def notify(title: str, message: str = "", priority: str = "default", tags: str = "", kind: str = "info",
           media_kind: Optional[str] = None, log_history: bool = True, meta: Optional[dict] = None) -> None:
    if log_history:
        log_event(kind=kind, title=title, message=message, media_kind=media_kind, meta=meta)
    url = (settings.NTFY_URL or "").rstrip("/")
    if not url:
        return
    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = tags
    if settings.NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {settings.NTFY_TOKEN}"
    try:
        import httpx

        httpx.post(f"{url}/{settings.NTFY_TOPIC}", content=message.encode("utf-8"), headers=headers, timeout=5)
    except Exception:
        log.warning("ntfy post failed (non-fatal)")
