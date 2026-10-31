"""Runtime key/value settings (override env defaults from the UI Settings page)."""
from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from .db import session_scope
from .models import Setting


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with session_scope() as s:
        row = s.get(Setting, key)
        return row.value if row else default


def set_setting(key: str, value: str) -> None:
    with session_scope() as s:
        row = s.get(Setting, key)
        if row:
            row.value = value
        else:
            row = Setting(key=key, value=value)
        s.add(row)


def all_settings() -> dict[str, str]:
    with session_scope() as s:
        return {r.key: r.value for r in s.exec(select(Setting)).all()}
