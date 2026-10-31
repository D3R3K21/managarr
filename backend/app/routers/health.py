"""System health checks."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from ..db import get_session
from ..services import health as svc

router = APIRouter()


@router.get("")
@router.get("/")
def health(session: Session = Depends(get_session)):
    results = svc.checks(session)
    overall = "ok"
    if any(r["status"] == "error" for r in results):
        overall = "error"
    elif any(r["status"] == "warn" for r in results):
        overall = "warn"
    return {"status": overall, "checks": results}
