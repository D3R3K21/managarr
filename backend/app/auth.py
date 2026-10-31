"""Auth: JWT bearer with admin/standard roles. The env ADMIN_USERNAME is the
bootstrap superadmin; additional accounts live in the User table (admin-managed).
The app also sits behind Cloudflare Access (Google SSO) on the public host, so this
is defense-in-depth + a login for LAN access."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .config import settings
from .db import get_session
from .models import DevicePairing, User

router = APIRouter()
ALGO = "HS256"
TTL = 60 * 60 * 24 * 30  # 30 days
_PBKDF2_ITER = 200_000


# ── password hashing (stdlib pbkdf2 — no external dependency) ──────────────────
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITER)
    return f"pbkdf2${_PBKDF2_ITER}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iter_s, salt_hex, hash_hex = stored.split("$")
        if scheme != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iter_s))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# ── tokens ────────────────────────────────────────────────────────────────────
def make_token(username: str, role: str = "user") -> str:
    now = int(time.time())
    return jwt.encode({"sub": username, "role": role, "iat": now, "exp": now + TTL},
                      settings.JWT_SECRET, algorithm=ALGO)


def _principal(authorization: Optional[str]) -> dict:
    """Decode the bearer token -> {"username", "role"}; raises 401 if invalid."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGO])
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid token")
    username = payload["sub"]
    # tokens issued before roles existed: the env admin stays admin, others standard.
    role = payload.get("role") or (
        "admin" if username.strip().lower() == settings.ADMIN_USERNAME.strip().lower() else "user")
    return {"username": username, "role": role}


def current_principal(authorization: Optional[str] = Header(None)) -> dict:
    """Any authenticated user; returns the full {"username","role"} principal."""
    return _principal(authorization)


def require_user(authorization: Optional[str] = Header(None)) -> str:
    """Any authenticated user. Returns the username."""
    return _principal(authorization)["username"]


def require_admin(authorization: Optional[str] = Header(None)) -> str:
    """Admin-only. Returns the username, or 403 for standard users."""
    p = _principal(authorization)
    if p["role"] != "admin":
        raise HTTPException(403, "admin privileges required")
    return p["username"]


# ── endpoints ─────────────────────────────────────────────────────────────────
class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginBody, session: Session = Depends(get_session)):
    # Usernames are case-insensitive (passwords are NOT). Stored lowercased.
    uname = (body.username or "").strip().lower()
    row = session.exec(select(User).where(User.username == uname)).first()
    if row and row.is_active and verify_password(body.password, row.password_hash):
        return {"token": make_token(row.username, row.role), "username": row.username, "role": row.role}
    if uname == settings.ADMIN_USERNAME.strip().lower() and body.password == settings.ADMIN_PASSWORD:
        return {"token": make_token(settings.ADMIN_USERNAME, "admin"),
                "username": settings.ADMIN_USERNAME, "role": "admin"}
    raise HTTPException(401, "bad credentials")


@router.get("/me")
def me(authorization: Optional[str] = Header(None)):
    return _principal(authorization)


# ── device authorization grant (RFC 8628-style) ───────────────────────────────
# TV/remote-averse device: start -> show user_code + QR -> poll for the token.
# A logged-in user approves the user_code from a phone/browser (scan or type it).
_DEVICE_CODE_TTL = 600            # pairing valid for 10 minutes
_DEVICE_POLL_INTERVAL = 3         # suggested poll cadence (seconds)
# Unambiguous alphabet — no 0/O/1/I/L so it's easy to read off a TV / type on a phone.
_USER_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _gen_user_code(n: int = 8) -> str:
    return "".join(secrets.choice(_USER_CODE_ALPHABET) for _ in range(n))


def _fmt_user_code(raw: str) -> str:
    return f"{raw[:4]}-{raw[4:]}" if len(raw) == 8 else raw


def _norm_user_code(s: str) -> str:
    return "".join(ch for ch in (s or "").upper() if ch in _USER_CODE_ALPHABET)


class DeviceStartBody(BaseModel):
    label: Optional[str] = None


@router.post("/device/start")
def device_start(body: Optional[DeviceStartBody] = None, session: Session = Depends(get_session)):
    """Unauthenticated: a device requests a pairing. Returns a secret device_code (the
    device holds it and polls) and a short user_code (shown to the human to approve)."""
    now = datetime.utcnow()
    device_code = secrets.token_urlsafe(32)
    raw = _gen_user_code()
    for _ in range(10):  # avoid colliding with another *pending* code
        clash = session.exec(
            select(DevicePairing).where(
                DevicePairing.user_code == raw, DevicePairing.status == "pending")
        ).first()
        if not clash:
            break
        raw = _gen_user_code()
    dp = DevicePairing(
        device_code=device_code, user_code=raw, status="pending",
        device_label=(body.label if body else None),
        created_at=now, expires_at=now + timedelta(seconds=_DEVICE_CODE_TTL))
    session.add(dp)
    session.commit()
    return {
        "device_code": device_code,
        "user_code": _fmt_user_code(raw),
        "expires_in": _DEVICE_CODE_TTL,
        "interval": _DEVICE_POLL_INTERVAL,
    }


@router.get("/device/poll")
def device_poll(device_code: str, session: Session = Depends(get_session)):
    """Unauthenticated: always 200 with a status. When approved, returns the JWT once
    (then the pairing is 'claimed')."""
    dp = session.exec(
        select(DevicePairing).where(DevicePairing.device_code == device_code)).first()
    if not dp:
        return {"status": "expired"}
    now = datetime.utcnow()
    if dp.status in ("pending", "approved") and now > dp.expires_at:
        dp.status = "expired"
        session.add(dp)
        session.commit()
    if dp.status == "approved":
        token = make_token(dp.username, dp.role or "user")
        dp.status = "claimed"
        session.add(dp)
        session.commit()
        return {"status": "approved", "token": token,
                "username": dp.username, "role": dp.role or "user"}
    return {"status": dp.status}


class DeviceApproveBody(BaseModel):
    user_code: str


def _find_pending(session: Session, user_code: str) -> DevicePairing:
    raw = _norm_user_code(user_code)
    dp = session.exec(select(DevicePairing).where(DevicePairing.user_code == raw)).first()
    if not dp:
        raise HTTPException(404, "code not found")
    if dp.status != "pending" or datetime.utcnow() > dp.expires_at:
        if dp.status == "pending":
            dp.status = "expired"
            session.add(dp)
            session.commit()
        raise HTTPException(410, "code expired or already used")
    return dp


@router.get("/device/lookup")
def device_lookup(user_code: str, principal: dict = Depends(current_principal),
                  session: Session = Depends(get_session)):
    """What a code is asking to authorize — so the approver sees it before confirming."""
    dp = _find_pending(session, user_code)
    return {"user_code": _fmt_user_code(dp.user_code), "device_label": dp.device_label,
            "requested_at": dp.created_at.isoformat()}


@router.post("/device/approve")
def device_approve(body: DeviceApproveBody, principal: dict = Depends(current_principal),
                   session: Session = Depends(get_session)):
    """Auth required: the logged-in user authorizes the device. The device then polls
    and receives a token carrying THIS user's identity + role."""
    dp = _find_pending(session, body.user_code)
    dp.status = "approved"
    dp.username = principal["username"]
    dp.role = principal["role"]
    dp.approved_at = datetime.utcnow()
    session.add(dp)
    session.commit()
    return {"ok": True, "device_label": dp.device_label}
