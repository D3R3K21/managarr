"""QualityProfile CRUD."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..auth import require_admin
from ..db import get_session
from ..models import QualityProfile

router = APIRouter()

_FIELDS = ("name", "media_kind", "min_seeders", "min_size_mb", "max_size_mb",
           "allowed_qualities", "preferred_words", "avoid_words", "is_default")


@router.get("")
def list_profiles(session: Session = Depends(get_session)):
    return session.exec(select(QualityProfile).order_by(QualityProfile.media_kind, QualityProfile.name)).all()


@router.get("/{profile_id}")
def get_profile(profile_id: int, session: Session = Depends(get_session)):
    p = session.get(QualityProfile, profile_id)
    if not p:
        raise HTTPException(404, "profile not found")
    return p


@router.post("")
def create_profile(body: dict, session: Session = Depends(get_session), user: str = Depends(require_admin)):
    p = QualityProfile(name=body.get("name", "Profile"), media_kind=body.get("media_kind", "movie"))
    for f in _FIELDS:
        if f in body:
            setattr(p, f, body[f])
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


@router.patch("/{profile_id}")
def update_profile(profile_id: int, body: dict, session: Session = Depends(get_session),
                   user: str = Depends(require_admin)):
    p = session.get(QualityProfile, profile_id)
    if not p:
        raise HTTPException(404, "profile not found")
    for f in _FIELDS:
        if f in body:
            setattr(p, f, body[f])
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


@router.delete("/{profile_id}")
def delete_profile(profile_id: int, session: Session = Depends(get_session), user: str = Depends(require_admin)):
    p = session.get(QualityProfile, profile_id)
    if not p:
        raise HTTPException(404, "profile not found")
    session.delete(p)
    session.commit()
    return {"ok": True}
