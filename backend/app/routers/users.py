"""User management — admin-only CRUD for additional login accounts.

The env ADMIN_USERNAME (bootstrap superadmin) is not a row here; it always exists
and is always admin. These endpoints manage the extra accounts.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..auth import hash_password, require_admin
from ..config import settings
from ..db import get_session
from ..models import User

router = APIRouter()
_ROLES = {"admin", "user"}


def _public(u: User) -> dict:
    return {"id": u.id, "username": u.username, "role": u.role,
            "is_active": u.is_active, "created_at": u.created_at}


@router.get("")
@router.get("/")
def list_users(session: Session = Depends(get_session), admin: str = Depends(require_admin)):
    """All managed accounts, plus the env bootstrap admin shown as a read-only row."""
    rows = session.exec(select(User).order_by(User.username)).all()
    bootstrap = {"id": None, "username": settings.ADMIN_USERNAME, "role": "admin",
                 "is_active": True, "created_at": None, "bootstrap": True}
    return [bootstrap] + [_public(u) for u in rows]


class CreateUser(BaseModel):
    username: str
    password: str
    role: str = "user"


@router.post("")
@router.post("/")
def create_user(body: CreateUser, session: Session = Depends(get_session),
                admin: str = Depends(require_admin)):
    # Usernames are case-insensitive — stored lowercased so the unique index and
    # login both ignore case. (Passwords stay case-sensitive.)
    username = (body.username or "").strip().lower()
    if not username or not body.password:
        raise HTTPException(400, "username and password are required")
    if body.role not in _ROLES:
        raise HTTPException(400, f"role must be one of {sorted(_ROLES)}")
    if username == settings.ADMIN_USERNAME.strip().lower():
        raise HTTPException(409, "that username is reserved (bootstrap admin)")
    if session.exec(select(User).where(User.username == username)).first():
        raise HTTPException(409, "username already exists")
    u = User(username=username, password_hash=hash_password(body.password), role=body.role)
    session.add(u)
    session.commit()
    session.refresh(u)
    return _public(u)


class UpdateUser(BaseModel):
    password: str | None = None
    role: str | None = None
    is_active: bool | None = None


@router.patch("/{user_id}")
def update_user(user_id: int, body: UpdateUser, session: Session = Depends(get_session),
                admin: str = Depends(require_admin)):
    """Reset password / change role / (de)activate an account."""
    u = session.get(User, user_id)
    if not u:
        raise HTTPException(404, "user not found")
    if body.role is not None:
        if body.role not in _ROLES:
            raise HTTPException(400, f"role must be one of {sorted(_ROLES)}")
        u.role = body.role
    if body.password:
        u.password_hash = hash_password(body.password)
    if body.is_active is not None:
        u.is_active = bool(body.is_active)
    session.add(u)
    session.commit()
    session.refresh(u)
    return _public(u)


@router.delete("/{user_id}")
def delete_user(user_id: int, session: Session = Depends(get_session),
                admin: str = Depends(require_admin)):
    u = session.get(User, user_id)
    if not u:
        raise HTTPException(404, "user not found")
    session.delete(u)
    session.commit()
    return {"deleted": user_id}
