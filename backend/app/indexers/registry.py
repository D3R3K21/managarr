"""Indexer registry — instantiates enabled ``Indexer`` rows into clients and
fans a query out across the ones that serve a given media kind."""
from __future__ import annotations

import logging

from sqlmodel import Session, select

from ..models import Indexer as IndexerRow
from .base import Indexer
from .iptorrents import IPTorrentsIndexer
from .mam import MAMIndexer

log = logging.getLogger("managarr.indexers.registry")

_CLASSES: dict[str, type[Indexer]] = {
    "mam": MAMIndexer,
    "iptorrents": IPTorrentsIndexer,
}

# Which indexer kinds serve which media kind.
_KIND_MAP: dict[str, set[str]] = {
    "audiobook": {"mam"},
    "movie": {"iptorrents"},
    "tv": {"iptorrents"},
}


def get_indexers(media_kind: str, session: Session) -> list[Indexer]:
    """Return instantiated, enabled indexers that serve ``media_kind``."""
    allowed = _KIND_MAP.get(media_kind, set())
    rows = session.exec(select(IndexerRow).where(IndexerRow.enabled == True)).all()  # noqa: E712
    out: list[Indexer] = []
    for row in rows:
        if allowed and row.kind not in allowed:
            continue
        cls = _CLASSES.get(row.kind)
        if not cls:
            log.warning("no indexer class for kind %r", row.kind)
            continue
        try:
            out.append(cls(config=row.config or {}))
        except Exception:
            log.exception("failed to instantiate indexer %s", row.name)
    return out


def search_all(media_kind: str, query: str, session: Session, author: str = "") -> list[dict]:
    """Search every enabled indexer for the kind, concatenate normalized releases."""
    results: list[dict] = []
    for ix in get_indexers(media_kind, session):
        try:
            results.extend(ix.search(query, media_kind, author=author))
        except Exception:
            log.exception("indexer %s search failed", getattr(ix, "name", "?"))
    results.sort(key=lambda r: -(r.get("seeders") or 0))
    return results
