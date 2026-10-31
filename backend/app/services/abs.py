"""Audiobookshelf scan trigger.

Managarr and ABS share the same audiobook folder on disk, but ABS only notices a
new book via its own scheduled scan or filesystem watcher. After managarr imports
an audiobook we POST ``/api/libraries/{id}/scan`` so the book appears promptly.

Best-effort and non-fatal: any failure is logged, never raised into the import.
Config: ABS_URL + ABS_TOKEN (admin API key); ABS_LIBRARY optionally pins a library
by name, else the first ``mediaType=book`` library is used. The resolved id is
cached for the process.
"""
from __future__ import annotations

import logging

from ..config import settings
from ..http import client

log = logging.getLogger("managarr.abs")

_LIB_ID: str | None = None  # cached resolved book-library id


def abs_configured() -> bool:
    return bool(settings.ABS_URL and settings.ABS_TOKEN)


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.ABS_TOKEN}", "Accept": "application/json"}


def _base() -> str:
    return settings.ABS_URL.rstrip("/")


def _resolve_library_id() -> str | None:
    """The book library's id — pinned by ABS_LIBRARY name, else the first book
    library. Cached after the first successful lookup."""
    global _LIB_ID
    if _LIB_ID:
        return _LIB_ID
    try:
        with client(timeout=15) as c:
            r = c.get(f"{_base()}/api/libraries", headers=_headers())
            r.raise_for_status()
            libs = (r.json() or {}).get("libraries", []) or []
    except Exception as exc:  # noqa: BLE001
        log.warning("ABS libraries fetch failed: %s", exc)
        return None
    want = (settings.ABS_LIBRARY or "").strip().lower()
    chosen = None
    if want:
        chosen = next((l for l in libs if (l.get("name") or "").lower() == want), None)
    if not chosen:
        chosen = next((l for l in libs if l.get("mediaType") == "book"), None)
    if not chosen and libs:
        chosen = libs[0]
    _LIB_ID = chosen.get("id") if chosen else None
    return _LIB_ID


def trigger_scan() -> bool:
    """Ask ABS to scan its book library. Returns True if the request was accepted."""
    if not abs_configured():
        return False
    lib_id = _resolve_library_id()
    if not lib_id:
        log.info("ABS scan skipped — no book library resolved")
        return False
    try:
        with client(timeout=20) as c:
            r = c.post(f"{_base()}/api/libraries/{lib_id}/scan", headers=_headers())
        if r.status_code in (200, 202):
            log.info("ABS scan triggered for library %s", lib_id)
            return True
        log.warning("ABS scan returned %s: %s", r.status_code, (r.text or "")[:120])
        return False
    except Exception as exc:  # noqa: BLE001
        log.warning("ABS scan request failed: %s", exc)
        return False


def clean_titles(dry_run: bool = False) -> dict:
    """Scrub retail fluff from every book title in ABS (see ``metadata.titles``).

    Idempotent: only titles the rules would change are PATCHed, so it is cheap to run on
    a schedule and catches books however they arrived (managarr import, hub request,
    manual drop). ABS stores the edit in the item's ``metadata.json``, which outranks the
    audio tags, so a rescan does not bring the old title back.
    """
    from ..metadata.titles import clean_title

    if not abs_configured():
        return {"skipped": "ABS not configured"}
    lib_id = _resolve_library_id()
    if not lib_id:
        return {"skipped": "no book library"}
    changed: list[dict] = []
    failed = 0
    with client(timeout=60) as c:
        r = c.get(f"{_base()}/api/libraries/{lib_id}/items", params={"limit": 0, "minified": 1},
                  headers=_headers())
        r.raise_for_status()
        items = (r.json() or {}).get("results", []) or []
        for it in items:
            old = (((it.get("media") or {}).get("metadata") or {}).get("title")) or ""
            new = clean_title(old)
            if not old or new == old:
                continue
            changed.append({"id": it.get("id"), "old": old, "new": new})
            if dry_run:
                continue
            try:
                p = c.patch(f"{_base()}/api/items/{it['id']}/media", headers=_headers(),
                            json={"metadata": {"title": new}})
                p.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                failed += 1
                log.warning("ABS title update failed for %s (%r): %s", it.get("id"), old, exc)
    if changed and not dry_run:
        log.info("ABS titles cleaned: %d (%d failed)", len(changed) - failed, failed)
    return {"checked": len(items), "changed": len(changed) - failed, "failed": failed,
            "dry_run": dry_run, "titles": changed}
