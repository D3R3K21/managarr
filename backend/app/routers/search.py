"""Global search — library + external catalogs, with add-to-library (+ download)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ..auth import require_user
from ..db import get_session
from ..services import search as svc

router = APIRouter()


@router.get("")
@router.get("/")
def do_search(q: str = Query("", min_length=0),
             kind: Optional[str] = Query(None, description="movie|tv|audiobook (omit for all)"),
             library: bool = Query(True, description="include items already in the library"),
             session: Session = Depends(get_session)):
    """One ranked list of library + external (TMDB/Audible) matches, closest-first.
    ``kind`` restricts to one media type; ``library=false`` shows only items you don't
    already own."""
    kinds = [kind] if kind in ("movie", "tv", "audiobook") else None
    return svc.search(session, q, kinds=kinds, include_library=library)


@router.post("/add")
def add(body: dict, user: str = Depends(require_user)):
    """Add an external result to the library (monitored) and optionally queue a
    download. Body: {kind, provider, provider_id, title, year?, author?, series?, download?}."""
    try:
        return svc.add_item(
            kind=body.get("kind"),
            provider=body.get("provider", "tmdb"),
            provider_id=str(body.get("provider_id") or ""),
            title=body.get("title") or "",
            year=body.get("year"),
            author=body.get("author", "") or "",
            series=body.get("series", "") or "",
            download=bool(body.get("download", True)),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
