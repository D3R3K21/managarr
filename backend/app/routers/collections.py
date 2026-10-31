"""Collections — franchises (movies) + audiobook series, with missing-detection."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import Session, select

from ..auth import require_user
from ..db import get_session
from ..models import Audiobook, Collection, CollectionMember, Movie
from ..services import collections as svc

router = APIRouter()


@router.get("")
@router.get("/")
def list_collections(kind: Optional[str] = Query(None), session: Session = Depends(get_session)):
    stmt = select(Collection)
    if kind:
        stmt = stmt.where(Collection.kind == kind)
    colls = session.exec(stmt.order_by(Collection.name)).all()
    # owned/missing counts per collection so the UI can filter "incomplete" without
    # fetching each collection's detail.
    counts: dict[int, list[int]] = {}
    for cid, owned in session.exec(
        select(CollectionMember.collection_id, CollectionMember.owned)
    ).all():
        c = counts.setdefault(cid, [0, 0])
        c[0] += 1
        if owned:
            c[1] += 1
    out = []
    for c in colls:
        total, owned = counts.get(c.id, [0, 0])
        d = c.model_dump()
        d["owned_count"] = owned
        d["missing_count"] = total - owned
        out.append(d)
    return out


@router.get("/missing")
def list_missing(kind: Optional[str] = Query(None), session: Session = Depends(get_session)):
    return svc.missing(session, kind=kind)


@router.get("/by-author")
def by_author(author: str = Query(...), session: Session = Depends(get_session)):
    return svc.by_author(author, session)


@router.get("/{collection_id}")
def get_collection(collection_id: int, session: Session = Depends(get_session)):
    coll = session.get(Collection, collection_id)
    if not coll:
        raise HTTPException(404, "collection not found")
    members = session.exec(
        select(CollectionMember).where(CollectionMember.collection_id == collection_id)
        .order_by(CollectionMember.position)
    ).all()
    return {
        "collection": coll,
        "owned": [m for m in members if m.owned],
        "missing": [m for m in members if not m.owned],
    }


@router.post("/rebuild")
def rebuild(kind: Optional[str] = Query(None),
            include_missing: bool = Query(False),
            user: str = Depends(require_user)):
    """Kick off an async rebuild (returns immediately; poll /rebuild/status).

    ``include_missing`` also runs the throttled TMDB/Audible missing-detection pass.
    The rebuild reads only Managarr's own DB — no external manager is required."""
    started = svc.start_rebuild(kind=kind, include_missing=include_missing)
    if not started:
        return {"started": False, "reason": "already_running", "progress": svc.REBUILD_PROGRESS}
    return {"started": True, "progress": svc.REBUILD_PROGRESS}


@router.get("/rebuild/status")
def rebuild_status():
    return svc.REBUILD_PROGRESS


@router.post("/migrate/radarr")
def migrate_radarr(request: Request, session: Session = Depends(get_session)):
    """ONE-TIME bulk import of movie franchises from Radarr, then rebuild grouping.

    Localhost-only (no bearer needed) so it can be driven by a user Script — the
    intended way to run this once before deleting Radarr. Runs synchronously and
    returns a summary so the script's run log shows the result."""
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(403, "migrate is localhost-only (run it from a user script)")
    out = {"migrate": svc.migrate_from_radarr(session)}
    out["rebuild"] = svc.rebuild_movie_collections(session)
    return out


@router.post("/{collection_id}/resync-plex")
def resync_collection_plex(collection_id: int, session: Session = Depends(get_session),
                           user: str = Depends(require_user)):
    """Resync this collection's members from Plex (adopt matches fixed in Plex), then
    re-group + prune. If the members re-match elsewhere and this collection empties,
    it's deleted (`collection_deleted: true`)."""
    try:
        return svc.resync_collection_from_plex(collection_id, session)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.delete("/{collection_id}")
def delete_collection(collection_id: int, session: Session = Depends(get_session),
                      user: str = Depends(require_user)):
    """Delete a collection + its members (e.g. a bogus collection from a mismatch).
    Owned items are untouched (only the grouping is removed)."""
    coll = session.get(Collection, collection_id)
    if not coll:
        raise HTTPException(404, "collection not found")
    back = Movie if coll.kind == "movie" else Audiobook
    for it in session.exec(select(back).where(back.collection_id == collection_id)).all():
        it.collection_id = None
        session.add(it)
    for m in session.exec(select(CollectionMember).where(CollectionMember.collection_id == collection_id)).all():
        session.delete(m)
    session.delete(coll)
    session.commit()
    return {"deleted": collection_id}


@router.post("/missing/{member_id}/grab")
def grab_missing(member_id: int, session: Session = Depends(get_session),
                 user: str = Depends(require_user)):
    try:
        grab = svc.grab_missing(member_id, session)
    except ValueError as e:
        raise HTTPException(404, str(e))
    if not grab:
        raise HTTPException(404, "no release passed the quality profile")
    return {"grabbed": True, "grab_id": grab.id, "title": grab.title}


@router.patch("/{collection_id}/monitored")
def set_collection_monitored(collection_id: int, body: dict,
                             session: Session = Depends(get_session),
                             user: str = Depends(require_user)):
    """Mark a collection "watched" — when monitored, the auto-download queue fills
    its missing members (throttled by the auto-download cap)."""
    coll = session.get(Collection, collection_id)
    if not coll:
        raise HTTPException(404, "collection not found")
    coll.monitored = bool(body.get("monitored", True))
    session.add(coll)
    session.commit()
    return {"id": collection_id, "monitored": coll.monitored}
