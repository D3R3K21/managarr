"""Release calendar — events in a range, manual sync, per-event auto_grab toggle."""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from ..auth import require_user
from ..db import get_session
from ..models import ReleaseEvent
from ..services import calendar as svc

router = APIRouter()


@router.get("")
@router.get("/")
def list_events(start: str = Query(None), end: str = Query(None),
                session: Session = Depends(get_session)):
    if not start:
        start = (date.today() - timedelta(days=7)).isoformat()
    if not end:
        end = (date.today() + timedelta(days=60)).isoformat()
    return svc.events(session, start, end)


@router.post("/sync")
def sync(session: Session = Depends(get_session), user: str = Depends(require_user)):
    return svc.sync_calendar(session)


@router.patch("/{event_id}")
def update_event(event_id: int, body: dict, session: Session = Depends(get_session),
                 user: str = Depends(require_user)):
    ev = session.get(ReleaseEvent, event_id)
    if not ev:
        raise HTTPException(404, "event not found")
    if "auto_grab" in body:
        ev.auto_grab = bool(body["auto_grab"])
    session.add(ev)
    session.commit()
    return {"id": ev.id, "auto_grab": ev.auto_grab}
