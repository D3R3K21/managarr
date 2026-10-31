"""Resync a library item's match from Plex.

Managarr matches off the *filesystem* (folder name / NFO / TMDB search), so when you
fix a wrong match **in Plex**, Managarr never sees it. Plex stores the corrected
match as external ids on the item (``Guid[]`` = ``tmdb://…``/``imdb://…``/``tvdb://…``).
This module finds the Plex item that points at the *same file* as a Managarr item,
reads Plex's ids, pins the right one, and re-pulls metadata — so "resync from Plex"
adopts exactly what Plex shows.

Movies + TV only (audiobooks live in AudiobookShelf, not Plex).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from urllib.parse import quote

from sqlmodel import Session, select

from ..config import settings
from ..models import Movie, Series
from ..metadata.aggregator import refresh_metadata
from .watched_mover import _plex

log = logging.getLogger("managarr.plex_sync")

# Live progress for the bulk resync, surfaced via GET /api/library/resync-plex/status.
RESYNC_PROGRESS: dict = {"running": False}


def plex_configured() -> bool:
    return bool(settings.PLEX_URL and settings.PLEX_TOKEN)


def _guid_ids(meta: dict) -> dict:
    """``Guid[]`` -> {'tmdb': '492', 'imdb': 'tt…', 'tvdb': '2356'}."""
    out: dict[str, str] = {}
    for g in meta.get("Guid", []) or []:
        gid = g.get("id", "")
        if "://" in gid:
            scheme, val = gid.split("://", 1)
            out[scheme] = val
    return out


def _sections(types: tuple[str, ...]) -> list[dict]:
    secs = _plex("/library/sections")["MediaContainer"].get("Directory", [])
    return [d for d in secs if d.get("type") in types]


def partial_scan(path: str) -> bool:
    """Ask Plex to scan just ``path`` (a folder inside one of its libraries).

    Plex normally learns about new files from filesystem events, but those never
    fire for writes that arrive over NFS from another pod (which is how every
    Managarr import lands), and its periodic full scan is off. So after an import
    we point Plex at the exact folder — the same thing "Scan Library Files" does,
    scoped to one show/movie. Best-effort: returns False (and logs) on any error.
    """
    if not plex_configured():
        return False
    try:
        for sec in _sections(("movie", "show")):
            for loc in sec.get("Location", []) or []:
                root = (loc.get("path") or "").rstrip("/")
                if root and (path == root or path.startswith(root + "/")):
                    # `path` is also _plex's URL parameter, so encode it into the query.
                    _plex(f"/library/sections/{sec['key']}/refresh?path={quote(path)}")
                    log.info("plex partial scan: %s -> section %s (%s)", path, sec["key"], sec.get("title"))
                    return True
        log.warning("plex partial scan: no library section contains %s", path)
    except Exception as exc:  # noqa: BLE001
        log.warning("plex partial scan of %s failed: %s", path, exc)
    return False


def _movie_index() -> dict[str, dict]:
    """{folder_path -> {ratingKey, title, year}} for every Plex movie, keyed by the
    movie's containing folder (matches Managarr ``Movie.folder_path``)."""
    idx: dict[str, dict] = {}
    for d in _sections(("movie",)):
        data = _plex(f"/library/sections/{d['key']}/all", type=1)
        for m in data["MediaContainer"].get("Metadata", []):
            entry = {"ratingKey": m.get("ratingKey"), "title": m.get("title"), "year": m.get("year")}
            for med in m.get("Media", []):
                for part in med.get("Part", []):
                    f = part.get("file")
                    if f:
                        idx.setdefault(os.path.dirname(f.rstrip("/")), entry)
    return idx


def _show_index() -> dict[str, dict]:
    """{folder_path -> {ratingKey, title, year}} for every Plex show, keyed by its
    on-disk Location (matches Managarr ``Series.folder_path``)."""
    idx: dict[str, dict] = {}
    for d in _sections(("show",)):
        data = _plex(f"/library/sections/{d['key']}/all")
        for m in data["MediaContainer"].get("Metadata", []):
            entry = {"ratingKey": m.get("ratingKey"), "title": m.get("title"), "year": m.get("year")}
            for loc in m.get("Location", []) or []:
                if loc.get("path"):
                    idx.setdefault(loc["path"].rstrip("/"), entry)
    return idx


def _best_match(kind: str, guids: dict) -> tuple[str, str] | None:
    """Pick the provider id to pin. Movies/TV are canonical-TMDB in Managarr, and
    enrich resolves TVDB/IMDb cross-links from it, so TMDB wins; fall back to TVDB."""
    if guids.get("tmdb"):
        return "tmdb", guids["tmdb"]
    if kind == "tv" and guids.get("tvdb"):
        return "tvdb", guids["tvdb"]
    if guids.get("tvdb"):
        return "tvdb", guids["tvdb"]
    if guids.get("imdb"):
        return "imdb", guids["imdb"]
    return None


def _apply(item, kind: str, provider: str, pid: str, session: Session) -> dict:
    """Pin the Plex-derived id and re-pull metadata from it (same path as Fix-match)."""
    meta = dict(item.meta or {})
    provs = dict(meta.get("providers") or {})
    provs[provider] = {"id": pid}
    meta["providers"] = provs
    meta["meta_source"] = "plex_resync"
    item.meta = meta
    if hasattr(item, "last_scanned"):
        item.last_scanned = datetime.utcnow()
    session.add(item)
    session.commit()
    refresh_metadata(item.id, kind, session)
    return {"provider": provider, "id": pid}


def resync_item(kind: str, item_id: int, session: Session,
                movie_idx: dict | None = None, show_idx: dict | None = None) -> dict:
    """Resync one movie/series from Plex. Returns a small result dict."""
    if not plex_configured():
        return {"ok": False, "reason": "plex_not_configured"}
    if kind in ("movie", "movies"):
        item = session.get(Movie, item_id)
        kind = "movie"
        idx = movie_idx if movie_idx is not None else _movie_index()
    elif kind in ("tv", "series"):
        item = session.get(Series, item_id)
        kind = "tv"
        idx = show_idx if show_idx is not None else _show_index()
    else:
        return {"ok": False, "reason": "kind must be movie|tv"}
    if not item:
        return {"ok": False, "reason": "not_found"}

    entry = idx.get((item.folder_path or "").rstrip("/"))
    if not entry:
        return {"ok": False, "reason": "no_plex_item_for_path", "folder": item.folder_path}

    full = _plex(f"/library/metadata/{entry['ratingKey']}")
    fm = (full or {}).get("MediaContainer", {}).get("Metadata", [{}])[0]
    guids = _guid_ids(fm)
    best = _best_match(kind, guids)
    if not best:
        return {"ok": False, "reason": "plex_has_no_external_id", "plex_title": entry.get("title")}

    provider, pid = best
    cur = ((item.meta or {}).get("providers") or {}).get(provider, {}).get("id")
    applied = _apply(item, kind, provider, pid, session)
    session.refresh(item)
    return {"ok": True, "plex_title": entry.get("title"), "changed": str(cur) != str(pid),
            "from": cur, "to": pid, "provider": provider, "new_title": item.title}


# ── bulk ──────────────────────────────────────────────────────────────────────
def resync_all(session: Session, kinds: tuple[str, ...] = ("movie", "tv")) -> dict:
    """Resync every movie/series from Plex (only those whose pinned id differs)."""
    RESYNC_PROGRESS.clear()
    RESYNC_PROGRESS.update(running=True, processed=0, total=0, changed=0,
                           started_at=datetime.utcnow().isoformat())
    movie_idx = _movie_index() if "movie" in kinds else {}
    show_idx = _show_index() if "tv" in kinds else {}
    rows: list[tuple[str, int]] = []
    if "movie" in kinds:
        rows += [("movie", m.id) for m in session.exec(select(Movie)).all()]
    if "tv" in kinds:
        rows += [("tv", s.id) for s in session.exec(select(Series)).all()]
    RESYNC_PROGRESS["total"] = len(rows)

    changed = matched = 0
    for i, (kind, item_id) in enumerate(rows, 1):
        try:
            r = resync_item(kind, item_id, session, movie_idx=movie_idx, show_idx=show_idx)
            if r.get("ok"):
                matched += 1
                if r.get("changed"):
                    changed += 1
        except Exception:
            log.exception("resync %s %s failed", kind, item_id)
        if i % 10 == 0:
            RESYNC_PROGRESS.update(processed=i, changed=changed)
    RESYNC_PROGRESS.update(running=False, processed=len(rows), changed=changed,
                           matched=matched, finished_at=datetime.utcnow().isoformat())
    return {"matched": matched, "changed": changed, "total": len(rows)}
