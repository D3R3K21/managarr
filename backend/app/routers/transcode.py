"""Transcode — candidates/queue listing, scan, approve/skip, process."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from ..auth import require_user
from ..db import get_session
from ..models import TranscodeJob
from ..services import transcode as svc

router = APIRouter()


@router.get("/candidates")
def candidates(session: Session = Depends(get_session)):
    return session.exec(
        select(TranscodeJob).where(TranscodeJob.status == "candidate")
        .order_by(TranscodeJob.source_size_bytes.desc())
    ).all()


@router.get("/queue")
def queue(status: Optional[str] = Query(None), session: Session = Depends(get_session)):
    stmt = select(TranscodeJob)
    if status:
        stmt = stmt.where(TranscodeJob.status == status)
    return session.exec(stmt.order_by(TranscodeJob.created_at.desc())).all()


@router.post("/scan")
def scan(session: Session = Depends(get_session), user: str = Depends(require_user)):
    return svc.scan_candidates(session)


@router.post("/approve/{job_id}")
def approve(job_id: int, user: str = Depends(require_user)):
    try:
        return svc.approve(job_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/skip/{job_id}")
def skip(job_id: int, user: str = Depends(require_user)):
    try:
        return svc.skip(job_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/process")
def process(session: Session = Depends(get_session), user: str = Depends(require_user)):
    return svc.process_queue(session)
