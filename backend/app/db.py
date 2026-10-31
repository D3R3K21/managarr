"""Synchronous SQLModel engine + session.

The app is deliberately synchronous: FastAPI runs `def` routes in a threadpool,
and most real work here is blocking anyway (filesystem scans, ffmpeg subprocess,
qBit calls). This avoids async-DB foot-guns for a single-user homelab app.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlmodel import Session, SQLModel, create_engine

from .config import settings

# psycopg (v3) sync driver. pool_pre_ping survives the PG pod restarting.
engine = create_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)


def init_db() -> None:
    # Import models so SQLModel.metadata is populated before create_all.
    from . import models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope() -> Iterator[Session]:
    """For use outside request scope (scheduler, background tasks)."""
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
