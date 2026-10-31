"""Playback devices — SMB copy targets (e.g. a Shield's on-device Plex library).

Device config CRUD + live free-space/contents reads + a progress-tracked transfer
with optional 'remove to make space' (delete_paths chosen live in the UI).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..auth import require_admin, require_user
from ..db import get_session
from ..models import DeviceCopyJob, PlexDevice
from ..services import device_copy as svc

router = APIRouter()


# ── request bodies ────────────────────────────────────────────────────────────
class DeviceIn(BaseModel):
    name: str
    location: Optional[str] = None
    host: str
    share: str = "internal"
    movies_path: str = "Movies"
    tv_path: str = "TV Shows"
    username: str = ""
    password: str = ""
    domain: str = "WORKGROUP"
    enabled: bool = True


class DevicePatch(BaseModel):
    name: Optional[str] = None
    location: Optional[str] = None
    host: Optional[str] = None
    share: Optional[str] = None
    movies_path: Optional[str] = None
    tv_path: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None          # only applied when non-empty
    domain: Optional[str] = None
    enabled: Optional[bool] = None


class CopyIn(BaseModel):
    media_kind: str
    item_id: int
    delete_paths: Optional[list[str]] = None   # files to remove on the device first


class DeletePathsIn(BaseModel):
    paths: list[str]


def _public(d: PlexDevice) -> dict:
    """Device row without the SMB password (write-only)."""
    return {"id": d.id, "name": d.name, "location": d.location, "host": d.host,
            "share": d.share, "movies_path": d.movies_path, "tv_path": d.tv_path,
            "username": d.username, "domain": d.domain, "enabled": d.enabled,
            "has_password": bool(d.password), "created_at": d.created_at}


def _get(session: Session, device_id: int) -> PlexDevice:
    d = session.get(PlexDevice, device_id)
    if not d:
        raise HTTPException(404, "device not found")
    return d


# ── collection ────────────────────────────────────────────────────────────────
@router.get("")
def list_devices(space: bool = False, session: Session = Depends(get_session),
                 user: str = Depends(require_user)):
    devs = session.exec(select(PlexDevice).order_by(PlexDevice.name)).all()
    out = []
    for d in devs:
        row = _public(d)
        if space and d.enabled:
            try:
                row.update(svc.free_space(d))
            except Exception as exc:  # noqa: BLE001 — offline device shouldn't break the list
                row["space_error"] = str(exc)[:160]
        out.append(row)
    return out


@router.post("")
def create_device(body: DeviceIn, session: Session = Depends(get_session),
                  user: str = Depends(require_admin)):
    d = PlexDevice(**body.model_dump())
    session.add(d)
    session.commit()
    session.refresh(d)
    return _public(d)


@router.get("/copy-jobs")
def copy_jobs(session: Session = Depends(get_session), user: str = Depends(require_user)):
    return svc.jobs(session)


# ── item ──────────────────────────────────────────────────────────────────────
@router.patch("/{device_id}")
def update_device(device_id: int, body: DevicePatch, session: Session = Depends(get_session),
                  user: str = Depends(require_admin)):
    d = _get(session, device_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        if k == "password" and not v:
            continue  # blank => keep existing
        setattr(d, k, v)
    session.add(d)
    session.commit()
    session.refresh(d)
    return _public(d)


@router.delete("/{device_id}")
def delete_device(device_id: int, session: Session = Depends(get_session),
                  user: str = Depends(require_admin)):
    d = _get(session, device_id)
    session.delete(d)
    session.commit()
    return {"deleted": device_id}


@router.post("/{device_id}/test")
def test_device(device_id: int, session: Session = Depends(get_session),
                user: str = Depends(require_user)):
    return svc.test_device(_get(session, device_id))


@router.get("/{device_id}/space")
def device_space(device_id: int, session: Session = Depends(get_session),
                 user: str = Depends(require_user)):
    try:
        return svc.free_space(_get(session, device_id))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"device unreachable: {exc}")


@router.get("/{device_id}/files")
def device_files(device_id: int, session: Session = Depends(get_session),
                 user: str = Depends(require_user)):
    try:
        return svc.list_files(_get(session, device_id))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"device unreachable: {exc}")


@router.delete("/{device_id}/files")
def device_delete_files(device_id: int, body: DeletePathsIn,
                        session: Session = Depends(get_session),
                        user: str = Depends(require_user)):
    return {"deleted": svc.delete_paths(_get(session, device_id), body.paths)}


@router.get("/{device_id}/plan")
def device_plan(device_id: int, media_kind: str, item_id: int,
                session: Session = Depends(get_session), user: str = Depends(require_user)):
    d = _get(session, device_id)
    try:
        return svc.plan(d, media_kind, item_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"device unreachable: {exc}")


@router.post("/{device_id}/copy")
def device_copy(device_id: int, body: CopyIn, session: Session = Depends(get_session),
                user: str = Depends(require_user)):
    _get(session, device_id)
    try:
        return svc.start_copy(device_id, body.media_kind, body.item_id, body.delete_paths)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
