"""Discover — surface things not in the library.

Movies / TV: TMDB trending minus what we already own (by tmdb id).
Audiobooks:  Audible catalog for monitored authors, minus owned (by asin/title).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from ..db import get_session
from ..metadata import providers as P
from ..models import Audiobook, Movie, Person, Series

router = APIRouter()


@router.get("/trailers")
def discover_trailers(tmdb_id: Optional[int] = Query(None), kind: str = Query("movie"),
                      q: str = Query("")):
    """Lazy trailer lookup for a discover/info card (so the list endpoints stay cheap).
    Returns ``[{site, key}]`` — same shape as meta.trailers on detail pages. With ``q``
    (title + year), falls back to a YouTube search when TMDB has no trailer — or when
    no tmdb_id is known at all."""
    trailers: list[dict] = []
    try:
        if tmdb_id is not None:
            trailers = P.tmdb_tv_trailers(tmdb_id) if kind == "tv" else P.tmdb_movie_trailers(tmdb_id)
        if not trailers and q.strip():
            trailers = P.youtube_search_trailers(q.strip())
        return {"trailers": trailers}
    except Exception:
        return {"trailers": trailers}


def _library_tmdb_ids(rows) -> set:
    ids = set()
    for r in rows:
        tid = ((r.meta or {}).get("providers") or {}).get("tmdb", {}).get("id")
        if tid:
            ids.add(int(tid))
    return ids


@router.get("/movies")
def discover_movies(session: Session = Depends(get_session)):
    owned = _library_tmdb_ids(session.exec(select(Movie)).all())
    trending = P.tmdb_trending("movie")
    return [{**m, "in_library": m.get("id") in owned} for m in trending if m.get("id") not in owned]


@router.get("/genres")
def discover_genres(kind: str = Query("movie")):
    """TMDB genre list for the year-browse filter."""
    return P.tmdb_genres(kind)


@router.get("/by-year")
def discover_by_year(year: int = Query(..., ge=1880, le=2100),
                     page: int = Query(1, ge=1),
                     kind: str = Query("movie"),
                     genre: Optional[int] = Query(None),
                     english: bool = Query(True),
                     session: Session = Depends(get_session)):
    """Browse ALL TMDB titles released in `year` (paginated), minus what we already
    own — a deep alternative to the small trending list. Movies + TV. ``genre`` is a
    TMDB genre id; ``english=true`` restricts to original-language English."""
    lang = "en" if english else None
    if kind == "tv":
        owned = _library_tmdb_ids(session.exec(select(Series)).all())
        data = P.tmdb_discover_tv(year=year, page=page, genre=genre, language=lang)
    else:
        owned = _library_tmdb_ids(session.exec(select(Movie)).all())
        data = P.tmdb_discover_movies(year=year, page=page, genre=genre, language=lang)
    results = [m for m in (data.get("results") or []) if m.get("id") not in owned]
    return {
        "year": year, "kind": kind,
        "page": data.get("page", page),
        "total_pages": data.get("total_pages", 1),
        "total_results": data.get("total_results"),
        "results": results,
    }


@router.get("/tv")
def discover_tv(session: Session = Depends(get_session)):
    owned = _library_tmdb_ids(session.exec(select(Series)).all())
    trending = P.tmdb_trending("tv")
    return [{**s, "in_library": s.get("id") in owned} for s in trending if s.get("id") not in owned]


@router.get("/audiobooks")
def discover_audiobooks(session: Session = Depends(get_session)):
    """Audible catalog for monitored authors, minus what we own."""
    owned = session.exec(select(Audiobook)).all()
    owned_asins = {(b.asin or "").upper() for b in owned if b.asin}
    owned_titles = {(b.title or "").lower().strip() for b in owned if b.title}

    # monitored authors: Person rows (role=author) + distinct author_names of monitored books
    authors: set[str] = set()
    for p in session.exec(select(Person).where(Person.role == "author")).all():
        if p.name:
            authors.add(p.name)
    for b in owned:
        if b.monitored and b.author_name:
            authors.add(b.author_name)

    results = []
    for author in sorted(authors):
        products = P.audible_catalog(author=author)
        missing = []
        for prod in products:
            if prod.get("format_type") and prod["format_type"] != "unabridged":
                continue
            if prod.get("language") and prod["language"] != "english":
                continue
            if prod.get("asin") and prod["asin"].upper() in owned_asins:
                continue
            if prod.get("title") and prod["title"].lower().strip() in owned_titles:
                continue
            missing.append(prod)
        if missing:
            results.append({"author": author, "missing": missing})
    return results
