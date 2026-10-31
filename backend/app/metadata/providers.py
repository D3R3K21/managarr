"""External metadata providers — each function returns a plain dict (or list of
dicts) and never raises into callers: on any network/parse failure it logs a
warning and returns an empty container.

All HTTP goes through ``app.http.client()`` (no VPN egress needed — these are
public APIs). Providers:

- TMDB   (movies / TV / collections / trailers / trending) — ``settings.TMDB_API_KEY``
- TVDB   (series + episodes, v4 — bearer token from login, cached)
- Audnexus (audiobook / author, no key) ``https://api.audnex.us``
- Audible catalog (no key) — enumerate an author's/series' full catalog
- Google Books + OpenLibrary (fallback synopsis / ids)
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

from ..config import settings
from ..http import client

log = logging.getLogger("managarr.providers")

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/original"
TVDB_BASE = "https://api4.thetvdb.com/v4"
AUDNEXUS_BASE = "https://api.audnex.us"
AUDIBLE_CATALOG_URL = "https://api.audible.com/1.0/catalog/products"
AUDIBLE_RESPONSE_GROUPS = "media,product_attrs,contributors,series"
GOOGLE_BOOKS_BASE = "https://www.googleapis.com/books/v1"
OPENLIBRARY_BASE = "https://openlibrary.org"

_UA = "Mozilla/5.0 (compatible; managarr)"


# ── TMDB ──────────────────────────────────────────────────────────────────────
def _tmdb_get(path: str, params: Optional[dict] = None) -> dict:
    if not settings.TMDB_API_KEY:
        log.warning("TMDB_API_KEY not configured")
        return {}
    q = {"api_key": settings.TMDB_API_KEY}
    if params:
        q.update(params)
    try:
        with client(timeout=15) as c:
            r = c.get(f"{TMDB_BASE}{path}", params=q)
            r.raise_for_status()
            return r.json() or {}
    except Exception as exc:
        log.warning("tmdb GET %s failed: %s", path, exc)
        return {}


def tmdb_search_movie(q: str) -> list[dict]:
    data = _tmdb_get("/search/movie", {"query": q, "include_adult": "false"})
    return data.get("results", []) or []


def tmdb_movie(id: int | str) -> dict:
    return _tmdb_get(f"/movie/{id}", {"append_to_response": "credits,external_ids"})


def tmdb_search_tv(q: str) -> list[dict]:
    data = _tmdb_get("/search/tv", {"query": q, "include_adult": "false"})
    return data.get("results", []) or []


def tmdb_tv(id: int | str) -> dict:
    return _tmdb_get(f"/tv/{id}", {"append_to_response": "credits,external_ids"})


def tmdb_tv_season(id: int | str, season_number: int) -> dict:
    """A season's episode list: {episodes: [{episode_number, name, air_date, ...}]}."""
    return _tmdb_get(f"/tv/{id}/season/{season_number}")


def tmdb_collection(id: int | str) -> dict:
    return _tmdb_get(f"/collection/{id}")


def tmdb_movie_trailers(id: int | str) -> list[dict]:
    """Return [{site, key, type}] for YouTube/Vimeo trailers + teasers."""
    data = _tmdb_get(f"/movie/{id}/videos")
    out = []
    for v in data.get("results", []) or []:
        if v.get("type") in ("Trailer", "Teaser") and v.get("key"):
            out.append({"site": v.get("site"), "key": v.get("key"), "type": v.get("type")})
    return out


def tmdb_tv_trailers(id: int | str) -> list[dict]:
    data = _tmdb_get(f"/tv/{id}/videos")
    out = []
    for v in data.get("results", []) or []:
        if v.get("type") in ("Trailer", "Teaser") and v.get("key"):
            out.append({"site": v.get("site"), "key": v.get("key"), "type": v.get("type")})
    return out


def tmdb_trending(kind: str) -> list[dict]:
    """kind: 'movie' | 'tv'."""
    k = "tv" if kind in ("tv", "series") else "movie"
    data = _tmdb_get(f"/trending/{k}/week")
    return data.get("results", []) or []


def tmdb_discover_movies(year: int | None = None, page: int = 1,
                         sort_by: str = "popularity.desc",
                         genre: int | str | None = None, language: str | None = None) -> dict:
    """Browse EVERY movie matching a filter (TMDB /discover/movie) — paginated.
    Returns the raw page dict {results, page, total_pages, total_results}."""
    params = {"sort_by": sort_by, "page": int(page), "include_adult": "false"}
    if year:
        params["primary_release_year"] = int(year)
    if genre:
        params["with_genres"] = str(genre)
    if language:
        params["with_original_language"] = language
    return _tmdb_get("/discover/movie", params)


def tmdb_discover_tv(year: int | None = None, page: int = 1,
                     sort_by: str = "popularity.desc",
                     genre: int | str | None = None, language: str | None = None) -> dict:
    """Browse TV (TMDB /discover/tv) by first-air-date year — paginated."""
    params = {"sort_by": sort_by, "page": int(page)}
    if year:
        params["first_air_date_year"] = int(year)
    if genre:
        params["with_genres"] = str(genre)
    if language:
        params["with_original_language"] = language
    return _tmdb_get("/discover/tv", params)


def tmdb_genres(kind: str = "movie") -> list[dict]:
    """TMDB genre list [{id, name}] for movie | tv."""
    k = "tv" if kind in ("tv", "series") else "movie"
    return _tmdb_get(f"/genre/{k}/list").get("genres", []) or []


# ── YouTube (no key — scrape the search results page) ─────────────────────────
YOUTUBE_RESULTS_URL = "https://www.youtube.com/results"
_YT_VIDEO_ID_RE = re.compile(r'"videoId":"([\w-]{11})"')
# The results page needs a real browser UA (and, in some regions, a consent
# cookie) to include the embedded ytInitialData JSON we pull videoIds from.
_YT_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
    "Cookie": "CONSENT=YES+cb",
}


def youtube_search_trailers(q: str, limit: int = 2) -> list[dict]:
    """Top YouTube hits for ``q`` + 'trailer' — fallback when TMDB has no trailer.
    Same [{site, key, type}] shape as the TMDB trailer helpers, so the frontend's
    embed grid works unchanged."""
    try:
        with client(timeout=15) as c:
            r = c.get(YOUTUBE_RESULTS_URL, params={"search_query": f"{q} trailer"},
                      headers=_YT_HEADERS)
            r.raise_for_status()
            html = r.text
    except Exception as exc:
        log.warning("youtube trailer search failed: %s", exc)
        return []
    keys: list[str] = []
    for vid in _YT_VIDEO_ID_RE.findall(html):
        if vid not in keys:
            keys.append(vid)
        if len(keys) >= limit:
            break
    return [{"site": "YouTube", "key": k, "type": "Search"} for k in keys]


# ── TVDB (v4 — bearer token cached) ───────────────────────────────────────────
_tvdb_token: Optional[str] = None
_tvdb_token_ts: float = 0.0
_TVDB_TTL = 60 * 60 * 12  # tokens are valid ~1 month; refresh well within


def _tvdb_get_token() -> str:
    global _tvdb_token, _tvdb_token_ts
    if _tvdb_token and (time.time() - _tvdb_token_ts) < _TVDB_TTL:
        return _tvdb_token
    if not settings.TVDB_API_KEY:
        log.warning("TVDB_API_KEY not configured")
        return ""
    try:
        with client(timeout=15) as c:
            r = c.post(f"{TVDB_BASE}/login", json={"apikey": settings.TVDB_API_KEY})
            r.raise_for_status()
            tok = (r.json().get("data") or {}).get("token", "")
        if tok:
            _tvdb_token = tok
            _tvdb_token_ts = time.time()
        return tok or ""
    except Exception as exc:
        log.warning("tvdb login failed: %s", exc)
        return ""


def _tvdb_get(path: str, params: Optional[dict] = None) -> dict:
    tok = _tvdb_get_token()
    if not tok:
        return {}
    try:
        with client(timeout=15) as c:
            r = c.get(f"{TVDB_BASE}{path}", headers={"Authorization": f"Bearer {tok}"}, params=params)
            r.raise_for_status()
            return r.json() or {}
    except Exception as exc:
        log.warning("tvdb GET %s failed: %s", path, exc)
        return {}


def tvdb_series(id: int | str) -> dict:
    return (_tvdb_get(f"/series/{id}/extended") or {}).get("data", {}) or {}


def tvdb_episodes(series_id: int | str) -> list[dict]:
    """Return all default-order episodes for a series (paginated)."""
    out: list[dict] = []
    page = 0
    while True:
        data = _tvdb_get(f"/series/{series_id}/episodes/default", {"page": page})
        block = data.get("data") or {}
        eps = block.get("episodes") or []
        out.extend(eps)
        # TVDB exposes pagination via links.next
        nxt = (data.get("links") or {}).get("next")
        if not nxt or not eps or page > 50:
            break
        page += 1
    return out


# ── Audnexus (no key) ─────────────────────────────────────────────────────────
def _audnexus_get(path: str, params: Optional[dict] = None) -> Any:
    try:
        with client(timeout=15) as c:
            r = c.get(f"{AUDNEXUS_BASE}{path}", params=params, headers={"User-Agent": _UA})
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        log.warning("audnexus GET %s failed: %s", path, exc)
        return None


def audnexus_book(asin: str, region: str = "us") -> dict:
    data = _audnexus_get(f"/books/{asin}", {"region": region})
    return data if isinstance(data, dict) else {}


def audnexus_author(asin: str, region: str = "us") -> dict:
    data = _audnexus_get(f"/authors/{asin}", {"region": region})
    return data if isinstance(data, dict) else {}


def audnexus_search_author(name: str, region: str = "us") -> list[dict]:
    data = _audnexus_get("/authors", {"name": name, "region": region})
    return data if isinstance(data, list) else []


# ── Audible catalog (no key) ──────────────────────────────────────────────────
def _map_audible_product(p: dict) -> dict:
    series = (p.get("series") or [None])[0] or {}
    images = p.get("product_images") or {}
    cover = images.get("500") or images.get("256") or images.get("512") or None
    return {
        "asin": p.get("asin"),
        "title": p.get("title", ""),
        "subtitle": p.get("subtitle"),
        "cover": cover,
        "publishedYear": (p.get("release_date") or "")[:4] or None,
        "authors": [{"name": a.get("name"), "asin": a.get("asin")} for a in (p.get("authors") or [])],
        "narrators": [n.get("name") for n in (p.get("narrators") or [])],
        "seriesName": series.get("title"),
        "seriesPosition": series.get("sequence"),
        "format_type": p.get("format_type"),
        "language": p.get("language"),
        "runtime_minutes": p.get("runtime_length_min"),
    }


def audible_catalog(author: str = "", keywords: str = "", num: int = 50, region: str = "us") -> list[dict]:
    """Enumerate an author's / series' Audible catalog -> mapped product dicts."""
    params: dict[str, str] = {
        "num_results": str(num),
        "products_sort_by": "Relevance",
        "response_groups": AUDIBLE_RESPONSE_GROUPS,
    }
    if author:
        params["author"] = author
    if keywords:
        params["keywords"] = keywords
    if not author and not keywords:
        return []
    try:
        with client(timeout=15) as c:
            r = c.get(AUDIBLE_CATALOG_URL, params=params, headers={"User-Agent": _UA})
            r.raise_for_status()
            products = (r.json() or {}).get("products", []) or []
    except Exception as exc:
        log.warning("audible catalog search failed: %s", exc)
        return []
    return [_map_audible_product(p) for p in products]


# ── Google Books ──────────────────────────────────────────────────────────────
def google_books_search(q: str) -> list[dict]:
    try:
        with client(timeout=15) as c:
            r = c.get(f"{GOOGLE_BOOKS_BASE}/volumes", params={"q": q, "maxResults": 20})
            r.raise_for_status()
            return (r.json() or {}).get("items", []) or []
    except Exception as exc:
        log.warning("google books search failed: %s", exc)
        return []


def google_books_volume(id: str) -> dict:
    try:
        with client(timeout=15) as c:
            r = c.get(f"{GOOGLE_BOOKS_BASE}/volumes/{id}")
            r.raise_for_status()
            return r.json() or {}
    except Exception as exc:
        log.warning("google books volume failed: %s", exc)
        return {}


# ── OpenLibrary ───────────────────────────────────────────────────────────────
def openlibrary_search(q: str) -> list[dict]:
    try:
        with client(timeout=15) as c:
            r = c.get(f"{OPENLIBRARY_BASE}/search.json", params={"q": q, "limit": 20})
            r.raise_for_status()
            return (r.json() or {}).get("docs", []) or []
    except Exception as exc:
        log.warning("openlibrary search failed: %s", exc)
        return []
