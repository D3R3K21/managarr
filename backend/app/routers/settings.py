"""Runtime settings (key/value overrides) + a redacted config view.

The redacted config view NEVER leaks secrets: token-ish fields are shown as a
boolean ``"set" / "not set"`` mask; only non-sensitive urls/paths/flags are shown
verbatim.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth import require_admin
from ..config import settings
from .. import settings_store

router = APIRouter()

# field names whose VALUES must never be returned (masked to set/not-set)
_SECRET_FIELDS = {
    "JWT_SECRET", "ADMIN_PASSWORD", "QBT_PASSWORD", "TMDB_API_KEY", "TVDB_API_KEY",
    "PLEX_TOKEN", "NTFY_TOKEN", "IPT_COOKIE",
}


@router.get("")
@router.get("/")
def get_runtime_settings():
    """Runtime key/value overrides set via the UI."""
    return settings_store.all_settings()


class SettingBody(BaseModel):
    key: str
    value: str


@router.put("")
@router.put("/")
def put_runtime_setting(body: SettingBody, user: str = Depends(require_admin)):
    settings_store.set_setting(body.key, body.value)
    # live-apply the auto-download enqueue cadence without a restart
    if body.key == "AUTO_DOWNLOAD_SYNC_INTERVAL_MINUTES":
        try:
            from ..scheduler.runner import reschedule_sync
            reschedule_sync()
        except Exception:  # noqa: BLE001
            pass
    return {"key": body.key, "value": body.value}


@router.get("/config")
def redacted_config():
    """A read-only, secret-masked view of the effective config."""
    out: dict = {}
    for name in dir(settings):
        if name.startswith("_") or not name.isupper():
            continue
        value = getattr(settings, name)
        if callable(value):
            continue
        if name in _SECRET_FIELDS:
            out[name] = "set" if value else "not set"
        else:
            out[name] = value
    return out
