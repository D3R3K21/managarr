"""Indexer CRUD + interactive search + connection test."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..auth import require_admin
from ..db import get_session
from ..indexers.registry import get_indexers, search_all
from ..models import Grab, Indexer, Movie

router = APIRouter()


@router.get("")
def list_indexers(session: Session = Depends(get_session)):
    return session.exec(select(Indexer).order_by(Indexer.name)).all()


@router.post("")
def create_indexer(body: dict, session: Session = Depends(get_session), user: str = Depends(require_admin)):
    ix = Indexer(
        name=body.get("name", "indexer"),
        kind=body.get("kind", "iptorrents"),
        enabled=bool(body.get("enabled", True)),
        categories=body.get("categories"),
        config=body.get("config") or {},
    )
    session.add(ix)
    session.commit()
    session.refresh(ix)
    return ix


@router.patch("/{indexer_id}")
def update_indexer(indexer_id: int, body: dict, session: Session = Depends(get_session),
                   user: str = Depends(require_admin)):
    ix = session.get(Indexer, indexer_id)
    if not ix:
        raise HTTPException(404, "indexer not found")
    for field in ("name", "kind", "enabled", "categories", "config"):
        if field in body:
            setattr(ix, field, body[field])
    session.add(ix)
    session.commit()
    session.refresh(ix)
    return ix


@router.delete("/{indexer_id}")
def delete_indexer(indexer_id: int, session: Session = Depends(get_session),
                   user: str = Depends(require_admin)):
    ix = session.get(Indexer, indexer_id)
    if not ix:
        raise HTTPException(404, "indexer not found")
    session.delete(ix)
    session.commit()
    return {"ok": True}


def _owned_release_keys(media_kind: str, item_id: int, session: Session) -> set[str]:
    """Normalized titles of the item's current copy: on-disk video basenames (imports
    keep the release filename) plus imported grab titles (covers renamed files)."""
    from ..pipeline.orchestrator import release_key
    from ..pipeline.scanner import VIDEO_EXTS

    keys: set[str] = set()
    if media_kind == "movie":
        m = session.get(Movie, item_id)
        if m and m.folder_path and os.path.isdir(m.folder_path):
            for f in os.listdir(m.folder_path):
                base, ext = os.path.splitext(f)
                if ext.lower() in VIDEO_EXTS:
                    keys.add(release_key(base))
    for g in session.exec(select(Grab).where(Grab.media_kind == media_kind,
                                             Grab.item_id == item_id,
                                             Grab.state == "imported")).all():
        keys.add(release_key(g.title))
    keys.discard("")
    return keys


@router.get("/search")
def search(media_kind: str = Query(...), q: str = Query(...), author: str = Query(""),
           item_id: int | None = Query(None),
           session: Session = Depends(get_session)):
    """Interactive search across enabled indexers -> normalized releases. With
    ``item_id`` (the library item the search is for), releases matching that item's
    current copy are flagged ``owned: true`` so the UI can stop a pointless re-grab
    (upgrading to the release you already have imports no new file and aborts)."""
    releases = search_all(media_kind, q, session, author=author)
    if item_id is not None:
        from ..pipeline.orchestrator import release_key
        owned = _owned_release_keys(media_kind, item_id, session)
        if owned:
            for r in releases:
                if release_key(r.get("title")) in owned:
                    r["owned"] = True
    return releases


@router.post("/test/{indexer_id}")
def test_indexer(indexer_id: int, session: Session = Depends(get_session),
                 user: str = Depends(require_admin)):
    ix = session.get(Indexer, indexer_id)
    if not ix:
        raise HTTPException(404, "indexer not found")
    # Map indexer row -> a representative media kind, do a trivial search.
    kind = "audiobook" if ix.kind == "mam" else "movie"
    for inst in get_indexers(kind, session):
        if inst.name == ix.name:
            try:
                results = inst.search("test", kind)
                return {"ok": True, "results": len(results)}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": "indexer disabled or unsupported"}
