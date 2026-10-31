"""Plex watchlist — cached list, manual sync, status."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from ..auth import require_user
from ..config import settings
from ..db import get_session
from ..services import watchlist as svc

router = APIRouter()


@router.get("")
@router.get("/")
def list_watchlist(session: Session = Depends(get_session)):
    return svc.list_watchlist(session)


@router.post("/sync")
def sync(session: Session = Depends(get_session), user: str = Depends(require_user)):
    return svc.sync_watchlist(session)


@router.get("/status")
def status(session: Session = Depends(get_session)):
    cached = svc.list_watchlist(session)
    return {
        "configured": bool(settings.PLEX_TOKEN),
        "plex_url": settings.PLEX_URL,
        "last_status": cached.get("status"),
        "users": cached.get("users", []),
        "item_count": len(cached.get("items", [])),
    }
