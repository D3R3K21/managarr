"""Library scan triggers (background) + last-scan status."""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends

from ..auth import require_user
from ..db import session_scope
from ..notify import notify
from ..pipeline import scanner

router = APIRouter()
log = logging.getLogger("managarr.scan")

# In-process status of the most recent scans (per kind).
_status: dict[str, dict] = {}


def _run(kind: str, fn) -> None:
    _status[kind] = {"running": True, "started_at": datetime.utcnow().isoformat()}
    try:
        with session_scope() as s:
            result = fn(s)
        _status[kind] = {"running": False, "finished_at": datetime.utcnow().isoformat(), **result}
        notify(f"Scan complete: {kind}", f"found {result.get('found', result)}", kind="scan",
               media_kind=kind, log_history=True)
    except Exception as exc:  # noqa: BLE001
        log.exception("%s scan failed", kind)
        _status[kind] = {"running": False, "finished_at": datetime.utcnow().isoformat(), "error": str(exc)}
        # clear the scanner's stale running flag so /status reflects the failure
        scanner.PROGRESS.setdefault(kind, {}).update(running=False, error=str(exc))
        return
    # After a successful scan, fetch metadata via providers for items that had no
    # on-disk meta file (auto-populates posters/overview without manual refresh).
    try:
        from ..metadata.aggregator import enrich_missing
        with session_scope() as s:
            enrich_missing(s, kinds=[kind])
    except Exception:
        log.exception("auto-enrich after %s scan failed", kind)
    # Keep collections current in real time: re-attach owned items and create any
    # new local series/franchises (API-free) so new books/movies show up without a
    # manual rebuild.
    try:
        from ..services import collections as coll_svc
        with session_scope() as s:
            if kind == "audiobook":
                coll_svc.rebuild_audiobook_series(s)
            elif kind == "movie":
                coll_svc.rebuild_movie_collections(s)
                coll_svc.relink_movie_owned(s)
    except Exception:
        log.exception("collection relink after %s scan failed", kind)


@router.post("/movies")
def scan_movies(bg: BackgroundTasks, user: str = Depends(require_user)):
    bg.add_task(_run, "movie", scanner.scan_movies)
    return {"status": "started", "kind": "movie"}


@router.post("/tv")
def scan_tv(bg: BackgroundTasks, user: str = Depends(require_user)):
    bg.add_task(_run, "tv", scanner.scan_tv)
    return {"status": "started", "kind": "tv"}


@router.post("/audiobooks")
def scan_audiobooks(bg: BackgroundTasks, user: str = Depends(require_user)):
    bg.add_task(_run, "audiobook", scanner.scan_audiobooks)
    return {"status": "started", "kind": "audiobook"}


@router.post("/all")
def scan_all(bg: BackgroundTasks, user: str = Depends(require_user)):
    bg.add_task(_run, "movie", scanner.scan_movies)
    bg.add_task(_run, "tv", scanner.scan_tv)
    bg.add_task(_run, "audiobook", scanner.scan_audiobooks)
    return {"status": "started", "kind": "all"}


@router.get("/status")
def status():
    # Merge router-level run state with the scanner's live processed/total progress,
    # and attach the metadata-enrich progress per kind under "enrich".
    from ..metadata import aggregator
    out: dict[str, dict] = {k: dict(v) for k, v in _status.items()}
    for kind, prog in scanner.PROGRESS.items():
        out[kind] = {**out.get(kind, {}), **prog}
    for kind, prog in aggregator.ENRICH_PROGRESS.items():
        out.setdefault(kind, {})["enrich"] = prog
    return out
