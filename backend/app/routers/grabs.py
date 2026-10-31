"""Grabs — manual grab, history, live queue, blocklist, import trigger."""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlmodel import Session, select

from ..auth import require_user
from ..config import settings
from ..db import get_session, session_scope
from ..models import (Audiobook, Blocklist, CollectionMember, DownloadQueueItem,
                      Grab, HistoryEvent, Movie, Series)
from ..pipeline import orchestrator
from ..pipeline.importer import import_completed
from ..pipeline.qbit import QBitClient

router = APIRouter()
log = logging.getLogger("managarr.grabs")


@router.post("")
def create_grab(body: dict, session: Session = Depends(get_session), user: str = Depends(require_user)):
    """Grab a chosen release.

    Body: {release: {...normalized...}, media_kind, item_id?, source?,
           save_path? | path-hints (title/year/season/author/series)}
    """
    release = body.get("release")
    media_kind = body.get("media_kind")
    if not release or not media_kind:
        raise HTTPException(400, "release and media_kind are required")

    # A placeholder "release" (no real indexer / download_url) means the caller wants
    # a search-and-grab, not a direct grab of an already-chosen release — that belongs
    # on POST /api/search/add (add to library + auto-download by title). Rejecting it
    # here with a clear message beats the opaque "no enabled indexer named 'search'".
    if release.get("indexer") in (None, "", "search") or not release.get("download_url"):
        raise HTTPException(
            422, "this endpoint grabs a specific chosen release; the given release has no "
                 "indexer/download_url. Use POST /api/search/add to add & auto-download by "
                 "title, or pick a real release from the title page.")

    save_path = body.get("save_path")
    if not save_path:
        save_path = orchestrator.build_save_path(
            media_kind,
            title=body.get("title") or release.get("title", "untitled"),
            year=body.get("year"),
            season=body.get("season"),
            author=body.get("author", ""),
            series=body.get("series", ""),
        )
    try:
        grab = orchestrator.grab(
            release, media_kind, save_path,
            item_id=body.get("item_id"), source=body.get("source", "manual"),
        )
    except Exception as exc:  # noqa: BLE001
        # Record the real reason: to the pod logs (with traceback) AND the History
        # tab — otherwise the failure is only visible in the browser's 502 body.
        title = release.get("title") or body.get("title") or "unknown release"
        log.exception("grab failed for %r (%s): %s", title, media_kind, exc)
        try:
            session.add(HistoryEvent(
                kind="failed", media_kind=media_kind, title=title,
                message=f"grab failed: {exc}",
                meta={"item_id": body.get("item_id"), "indexer": release.get("indexer"),
                      "source": body.get("source", "manual")}))
            session.commit()
        except Exception:  # noqa: BLE001
            log.exception("could not record grab-failure history event")
            session.rollback()
        raise HTTPException(502, f"grab failed: {exc}")
    return grab


@router.get("")
def history(limit: int = 50, offset: int = 0, kind: str | None = None,
            session: Session = Depends(get_session)):
    """Recent grabs + history events, paged (skip/take). ``kind`` filters events by
    type server-side (so filtering doesn't silently hide older rows past the page)."""
    grabs = session.exec(
        select(Grab).order_by(Grab.created_at.desc()).offset(offset).limit(limit)
    ).all()
    estmt = select(HistoryEvent)
    if kind and kind != "all":
        estmt = estmt.where(HistoryEvent.kind == kind)
    events = session.exec(
        estmt.order_by(HistoryEvent.created_at.desc()).offset(offset).limit(limit)
    ).all()
    return {"grabs": grabs, "events": events}


@router.get("/event-kinds")
def event_kinds(session: Session = Depends(get_session)):
    """Distinct history event kinds (for the History type filter), so the dropdown
    lists every type that exists, not just those on the current page."""
    rows = session.exec(select(HistoryEvent.kind).distinct()).all()
    return sorted({k for k in rows if k})


_DONE_STATES = {"uploading", "stalledUP", "queuedUP", "pausedUP", "checkingUP",
                "forcedUP", "completed"}


@router.get("/queue")
def queue():
    """Active downloads in the managarr category — excludes completed/seeding
    torrents (those belong in the library/history, not the live queue)."""
    qbit = QBitClient()
    torrents = qbit.torrents(category=settings.QBT_CATEGORY)
    out = []
    for t in torrents:
        # skip finished items (fully downloaded and now seeding)
        if (t.get("progress") or 0) >= 1.0 or t.get("state") in _DONE_STATES:
            continue
        out.append({
            "name": t.get("name"),
            "hash": t.get("hash"),
            "state": t.get("state"),
            "progress": t.get("progress"),
            "dlspeed": t.get("dlspeed"),
            "num_seeds": t.get("num_seeds"),
            "size": t.get("size"),
            "eta": t.get("eta"),
            "save_path": t.get("save_path"),
        })
    return out


@router.get("/completed")
def completed(limit: int = 50, offset: int = 0):
    """Completed downloads in the managarr category, newest-completed first, paged.
    The live /queue hides these, so this is where a finished torrent stays visible
    (instead of just disappearing). qBit does the filter/sort/limit/offset server-side
    so we never pull the whole category, however large it grows."""
    qbit = QBitClient()
    torrents = qbit.torrents(category=settings.QBT_CATEGORY, filter="completed",
                             sort="completion_on", reverse=True, limit=limit, offset=offset)
    return [{
        "name": t.get("name"),
        "hash": t.get("hash"),
        "state": t.get("state"),
        "size": t.get("size"),
        "ratio": t.get("ratio"),
        "completion_on": t.get("completion_on"),
        "num_seeds": t.get("num_seeds"),
        "save_path": t.get("save_path"),
    } for t in torrents]


@router.post("/import")
def trigger_import(bg: BackgroundTasks, user: str = Depends(require_user)):
    def _job():
        with session_scope() as s:
            import_completed(s)
    bg.add_task(_job)
    return {"status": "started"}


# ── auto-download queue (throttled) ───────────────────────────────────────────
_QUEUE_MODELS = {"movie": Movie, "tv": Series, "audiobook": Audiobook}


@router.get("/auto-queue")
def auto_queue(state: str | None = None, limit: int = 100, offset: int = 0,
               session: Session = Depends(get_session)):
    """The throttled auto-download queue. ``state`` filters (queued|active|done|
    no_release|parked|failed); default returns the open work (queued/active/
    no_release) newest-first within priority."""
    stmt = select(DownloadQueueItem)
    if state and state not in ("all", "actionable"):
        stmt = stmt.where(DownloadQueueItem.state == state)
    elif state != "all":
        # default ("actionable"): everything still worth showing — hide only the
        # completed ones (those live in the Queue/Completed + History views).
        stmt = stmt.where(DownloadQueueItem.state != "done")
    return session.exec(
        stmt.order_by(DownloadQueueItem.priority, DownloadQueueItem.created_at)
        .offset(offset).limit(limit)
    ).all()


@router.delete("/auto-queue/{qid}")
def cancel_auto_queue(qid: int, delete_files: bool = False,
                      session: Session = Depends(get_session),
                      user: str = Depends(require_user)):
    """Drop a queue item. If it's an active download, the in-flight qBit torrent is
    removed — with ``delete_files`` its downloaded data (e.g. partial files) too. And
    — the inverse of deleting a library item — if the linked library row is a
    file-less SHELL (a movie/book that only existed to be auto-downloaded), it's
    deleted too. Owned items with files (and TV series) are never auto-deleted here."""
    it = session.get(DownloadQueueItem, qid)
    if not it:
        raise HTTPException(404, "queue item not found")
    media_kind, item_id, state, infohash = it.media_kind, it.item_id, it.state, it.infohash

    if state == "active" and infohash:
        try:
            from ..pipeline.qbit import QBitClient
            QBitClient().delete_torrent(infohash, delete_files=delete_files)
        except Exception:  # noqa: BLE001
            pass
    session.delete(it)

    removed_item = None
    model = _QUEUE_MODELS.get(media_kind)
    if model and item_id is not None and media_kind in ("movie", "audiobook"):
        obj = session.get(model, item_id)
        fileless = obj is not None and (
            not obj.has_file if media_kind == "movie" else not obj.has_audio)
        if fileless:
            for mem in session.exec(
                select(CollectionMember).where(CollectionMember.item_kind == media_kind,
                                               CollectionMember.item_id == item_id)
            ).all():
                mem.owned = False
                mem.item_id = None
                session.add(mem)
            session.delete(obj)
            removed_item = item_id
    session.commit()
    return {"deleted": qid, "removed_item": removed_item, "media_kind": media_kind}


@router.post("/auto-queue/{qid}/retry")
def retry_auto_queue(qid: int, session: Session = Depends(get_session),
                     user: str = Depends(require_user)):
    """Re-queue a parked/failed item ("Search again") and clear the releaseless flag
    on its library item so it returns to the normal view."""
    it = session.get(DownloadQueueItem, qid)
    if not it:
        raise HTTPException(404, "queue item not found")
    it.state = "queued"
    it.attempts = 0
    it.next_retry_at = None
    it.last_error = None
    it.finished_at = None
    model = _QUEUE_MODELS.get(it.media_kind)
    if model and it.item_id:
        obj = session.get(model, it.item_id)
        if obj and (obj.meta or {}).get("acq_status") == "releaseless":
            meta = dict(obj.meta or {})
            meta.pop("acq_status", None)
            meta.pop("releaseless_at", None)
            obj.meta = meta
            session.add(obj)
    session.add(it)
    session.commit()
    session.refresh(it)
    return it


# ── blocklist ─────────────────────────────────────────────────────────────────
@router.get("/blocklist")
def list_blocklist(limit: int = 50, offset: int = 0, session: Session = Depends(get_session)):
    return session.exec(
        select(Blocklist).order_by(Blocklist.created_at.desc()).offset(offset).limit(limit)
    ).all()


@router.post("/blocklist")
def add_blocklist(body: dict, session: Session = Depends(get_session), user: str = Depends(require_user)):
    b = Blocklist(infohash=(body.get("infohash") or "").lower() or None,
                  title=body.get("title"), reason=body.get("reason"))
    session.add(b)
    session.commit()
    session.refresh(b)
    return b


@router.delete("/blocklist/{block_id}")
def delete_blocklist(block_id: int, session: Session = Depends(get_session), user: str = Depends(require_user)):
    b = session.get(Blocklist, block_id)
    if not b:
        raise HTTPException(404, "not found")
    session.delete(b)
    session.commit()
    return {"ok": True}
