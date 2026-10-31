"""Global search — one ranked list combining owned library items + external catalog
matches (TMDB / Audible), best-match-first, each tagged owned/addable.

`search()` returns {query, results: [...]}. For an item we both own AND found in a
catalog, the external (richer, scraped) representation wins and carries a
`library_id` so the UI can offer "view in library". `add_item()` creates a monitored
library shell from a provider id, enriches it, and optionally queues a background
grab — the same shell+auto_grab path as the watchlist sync.
"""
from __future__ import annotations

import logging
import os
import threading

from sqlmodel import Session, select

from ..db import session_scope
from ..models import Audiobook, Movie, Series
from ..metadata import providers as P
from ..metadata.aggregator import refresh_metadata
from ..pipeline import orchestrator

log = logging.getLogger("managarr.search")


def _lev(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a or not b:
        return len(a) or len(b)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + (0 if ca == cb else 1)))
        prev = cur
    return prev[-1]


def _score(query: str, title: str) -> float:
    """0..1 closeness — exact > prefix > substring > token overlap > edit distance."""
    q, t = (query or "").lower().strip(), (title or "").lower().strip()
    if not q or not t:
        return 0.0
    if q == t:
        return 1.0
    if t.startswith(q):
        return 0.92
    if q in t:
        return max(0.7, 0.88 - 0.005 * (len(t) - len(q)))
    if t in q:
        return 0.65
    qt, tt = set(q.split()), set(t.split())
    if qt & tt:
        return 0.45 + 0.2 * len(qt & tt) / max(len(qt | tt), 1)
    d = _lev(q, t)
    return max(0.0, 1 - d / max(len(q), len(t), 1)) * 0.5


def _library_index(session: Session) -> dict:
    """key -> library item id, where key identifies an owned item across sources."""
    idx: dict = {}
    for m in session.exec(select(Movie)).all():
        tid = ((m.meta or {}).get("providers") or {}).get("tmdb", {}).get("id")
        if tid is not None:
            idx[("movie", str(tid))] = m.id
    for s in session.exec(select(Series)).all():
        tid = ((s.meta or {}).get("providers") or {}).get("tmdb", {}).get("id")
        if tid is not None:
            idx[("tv", str(tid))] = s.id
    for b in session.exec(select(Audiobook)).all():
        if b.asin:
            idx[("audiobook", "asin:" + b.asin.upper())] = b.id
    return idx


def _lib_key(kind: str, provider_id) -> tuple:
    if kind == "audiobook":
        return (kind, "asin:" + str(provider_id or "").upper())
    return (kind, str(provider_id or ""))


def library_search(session: Session, q: str, kinds=None, limit: int = 25) -> list[dict]:
    ks = set(kinds) if kinds else None
    out: list[dict] = []

    def add(kind, row, title, year, poster, sub, sc, provider_id, provider, author=None):
        out.append({
            "kind": kind, "source": "library", "library_id": row.id, "owned": True,
            "title": title, "year": year, "cover_url": poster, "sub": sub,
            "overview": getattr(row, "overview", None), "author": author,
            "provider": provider, "provider_id": str(provider_id) if provider_id else None,
            "key": _lib_key(kind, provider_id) if provider_id else (kind, "id:%s" % row.id),
            "meta": row.meta or {}, "asin": getattr(row, "asin", None), "score": sc,
        })

    if ks is None or "movie" in ks:
        for m in session.exec(select(Movie)).all():
            sc = _score(q, m.title)
            if sc > 0.2:
                tid = ((m.meta or {}).get("providers") or {}).get("tmdb", {}).get("id")
                add("movie", m, m.title, m.year, m.poster_url, "Movie", sc, tid, "tmdb")
    if ks is None or "tv" in ks:
        for s in session.exec(select(Series)).all():
            sc = _score(q, s.title)
            if sc > 0.2:
                tid = ((s.meta or {}).get("providers") or {}).get("tmdb", {}).get("id")
                add("tv", s, s.title, s.year, s.poster_url, "TV series", sc, tid, "tmdb")
    if ks is None or "audiobook" in ks:
        for b in session.exec(select(Audiobook)).all():
            sc = max(_score(q, b.title), 0.7 * _score(q, b.author_name or ""))
            if sc > 0.2:
                add("audiobook", b, b.title, b.publish_year, b.cover_url, b.author_name or "Audiobook",
                    sc, b.asin, "audible", author=b.author_name)
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


def external_search(session: Session, q: str, idx: dict, kinds=None, limit: int = 10) -> list[dict]:
    ks = set(kinds) if kinds else None
    want = lambda k: ks is None or k in ks  # noqa: E731
    out: list[dict] = []

    def _yr(s):
        s = str(s or "")[:4]
        return int(s) if s.isdigit() else None

    if want("movie"):
        try:
            for r in (P.tmdb_search_movie(q) or [])[:limit]:
                tid = str(r.get("id"))
                key = ("movie", tid)
                out.append({"kind": "movie", "source": "tmdb", "provider": "tmdb", "provider_id": tid,
                            "key": key, "title": r.get("title"), "year": _yr(r.get("release_date")),
                            "cover_url": (P.TMDB_IMAGE_BASE + r["poster_path"]) if r.get("poster_path") else None,
                            "sub": "Movie", "overview": r.get("overview"),
                            "meta": {"providers": {"tmdb": {"id": tid}}},
                            "owned": key in idx, "library_id": idx.get(key), "score": _score(q, r.get("title"))})
        except Exception:
            log.warning("tmdb movie search failed", exc_info=True)
    if want("tv"):
        try:
            for r in (P.tmdb_search_tv(q) or [])[:limit]:
                tid = str(r.get("id"))
                key = ("tv", tid)
                out.append({"kind": "tv", "source": "tmdb", "provider": "tmdb", "provider_id": tid,
                            "key": key, "title": r.get("name"), "year": _yr(r.get("first_air_date")),
                            "cover_url": (P.TMDB_IMAGE_BASE + r["poster_path"]) if r.get("poster_path") else None,
                            "sub": "TV series", "overview": r.get("overview"),
                            "meta": {"providers": {"tmdb": {"id": tid}}},
                            "owned": key in idx, "library_id": idx.get(key), "score": _score(q, r.get("name"))})
        except Exception:
            log.warning("tmdb tv search failed", exc_info=True)
    if want("audiobook"):
        try:
            for r in (P.audible_catalog(keywords=q, num=limit) or []):
                asin = (r.get("asin") or "")
                key = ("audiobook", "asin:" + asin.upper())
                authors = ", ".join(a.get("name", "") for a in (r.get("authors") or []))
                out.append({"kind": "audiobook", "source": "audible", "provider": "audible",
                            "provider_id": asin, "key": key, "title": r.get("title"),
                            "year": _yr(r.get("publishedYear")), "cover_url": r.get("cover"),
                            "sub": authors or "Audiobook", "overview": r.get("subtitle"),
                            "author": authors, "series": r.get("seriesName"), "asin": asin,
                            "meta": {"providers": {"audible": {"asin": asin}}},
                            "owned": key in idx, "library_id": idx.get(key), "score": _score(q, r.get("title"))})
        except Exception:
            log.warning("audible search failed", exc_info=True)
    return out


def search(session: Session, q: str, kinds=None, include_library: bool = True) -> dict:
    q = (q or "").strip()
    if len(q) < 2:
        return {"query": q, "results": []}
    idx = _library_index(session)
    ext = external_search(session, q, idx, kinds=kinds)
    # External (richer/scraped) representation wins for items found both ways.
    results: list[dict] = list(ext)
    seen = {e["key"] for e in ext}
    if include_library:
        for it in library_search(session, q, kinds=kinds):
            if it["key"] not in seen:
                results.append(it)
                seen.add(it["key"])
    else:
        # "only things not already in my library" — drop everything owned.
        results = [r for r in results if not r.get("owned")]
    results.sort(key=lambda x: -x["score"])
    return {"query": q, "results": results}


def add_item(*, kind: str, provider: str, provider_id: str, title: str,
             year=None, author: str = "", series: str = "", download: bool = True) -> dict:
    """Create a monitored library shell from a provider id, enrich it, and (optional)
    queue a download in the background. Returns the new item's id/kind."""
    if kind not in ("movie", "tv", "audiobook"):
        raise ValueError("kind must be movie|tv|audiobook")
    if not (provider_id and title):
        raise ValueError("provider_id and title are required")

    with session_scope() as s:
        if kind == "movie":
            save = orchestrator.build_save_path("movie", title=title, year=year)
            shell = Movie(title=title, year=year, folder_path=save, monitored=True, has_file=False,
                          meta={"providers": {provider: {"id": provider_id}}, "source": "search"})
        elif kind == "tv":
            folder = orchestrator.build_save_path("tv", title=title, season=1)
            shell = Series(title=title, year=year, folder_path=os.path.dirname(folder), monitored=True,
                           meta={"providers": {provider: {"id": provider_id}}, "source": "search"})
        else:
            save = orchestrator.build_save_path("audiobook", title=title, author=author, series=series or "")
            pmeta = {"asin": provider_id} if provider == "audible" else {"id": provider_id}
            meta = {"providers": {provider: pmeta}, "source": "search"}
            if series:
                meta["scanned_series"] = series
            shell = Audiobook(title=title, author_name=author or None, folder_path=save,
                              monitored=True, has_audio=False,
                              asin=provider_id if provider == "audible" else None, meta=meta)
        s.add(shell)
        s.commit()
        s.refresh(shell)
        iid = shell.id
        try:
            refresh_metadata(iid, kind, s)
        except Exception:
            log.warning("enrich after add failed for %s %s", kind, iid, exc_info=True)

    if download:
        threading.Thread(target=_bg_grab,
                         args=(kind, iid, title, year, author, series), daemon=True).start()
    return {"id": iid, "kind": kind, "download": download}


def _bg_grab(kind, iid, title, year, author, series):
    try:
        if kind == "movie":
            save = orchestrator.build_save_path("movie", title=title, year=year)
            query = f"{title} {year}" if year else title
            orchestrator.auto_grab("movie", query, save, item_id=iid, source="search")
        elif kind == "tv":
            # Episodes were stubbed during enrich; enqueue the missing ones (season
            # packs) through the throttled queue rather than grabbing one episode.
            from ..pipeline import download_queue as dq
            with session_scope() as s:
                dq.enqueue_tv_for_series(s, iid)
            with session_scope() as s:
                dq.process_queue(s)
        else:
            save = orchestrator.build_save_path("audiobook", title=title, author=author, series=series or "")
            orchestrator.auto_grab("audiobook", title, save, author=author or "",
                                   item_id=iid, source="search")
    except Exception:
        log.exception("search add: background grab failed for %s %s", kind, iid)
