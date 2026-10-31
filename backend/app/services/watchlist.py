"""Plex watchlist sync for ALL managed / home users.

IMPORTANT — where the watchlist lives:
  The Plex *watchlist* is NOT stored on the local Media Server; it lives on
  plex.tv's "Discover" service. So we must call the Discover/metadata.provider
  endpoints with an *account* X-Plex-Token, not the local server token. The local
  ``PLEX_TOKEN`` is the owner's account token and works for the owner's own
  watchlist. Home/managed users each have their own token; we enumerate them via
  ``GET https://plex.tv/api/home/users`` and (best-effort) switch to each user's
  token. When per-user token switching is unavailable we fall back to syncing the
  owner watchlist only.

All network goes through ``app.http.client()`` (Discover is a public plex.tv host,
no VPN egress). Missing token -> return empty + a clear status message.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Optional

from sqlmodel import Session, select

from ..config import settings
from ..http import client
from ..models import HistoryEvent, Movie, Series
from ..notify import log_event
from ..pipeline import download_queue
from ..pipeline import orchestrator

log = logging.getLogger("managarr.watchlist")

PLEX_TV = "https://plex.tv"
DISCOVER_WATCHLIST = "https://discover.provider.plex.tv/library/sections/watchlist/all"
METADATA_WATCHLIST = "https://metadata.provider.plex.tv/library/sections/watchlist/all"

# in-memory cache of the last sync (list_watchlist reads this)
_LAST_SYNC: dict = {"items": [], "status": "never run", "users": []}


def _headers(token: str) -> dict:
    return {
        "X-Plex-Token": token,
        "X-Plex-Product": "Managarr",
        "X-Plex-Client-Identifier": "managarr-watchlist",
        "Accept": "application/json",
    }


def _home_users() -> list[dict]:
    """List home/managed users (id, title, token if exposed). Best-effort."""
    if not settings.PLEX_TOKEN:
        return []
    try:
        with client(timeout=15) as c:
            r = c.get(f"{PLEX_TV}/api/home/users", headers=_headers(settings.PLEX_TOKEN))
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            users: list[dict] = []
            if "xml" in ctype or r.text.lstrip().startswith("<"):
                root = ET.fromstring(r.text)
                for u in root.iter("User"):
                    users.append({
                        "id": u.get("id"),
                        "title": u.get("title") or u.get("username"),
                        "token": u.get("authToken") or u.get("token"),
                    })
            else:
                data = r.json() or {}
                for u in (data.get("users") or []):
                    users.append({
                        "id": u.get("id"),
                        "title": u.get("title") or u.get("username"),
                        "token": u.get("authToken") or u.get("token"),
                    })
            return users
    except Exception as exc:
        log.warning("home users fetch failed: %s", exc)
        return []


def _parse_guids(raw_guids) -> dict:
    """Plex guids look like ['tmdb://12345', 'imdb://tt...', 'tvdb://...']."""
    out: dict[str, str] = {}
    for g in raw_guids or []:
        gid = g.get("id") if isinstance(g, dict) else g
        if not gid or "://" not in gid:
            continue
        scheme, _, value = gid.partition("://")
        out[scheme] = value
    return out


def _watchlist_for_token(token: str) -> list[dict]:
    """Fetch a single account's watchlist items (movie/show + guids)."""
    items: list[dict] = []
    params = {"includeFields": "guid,title,year,type", "includeGuids": "1"}
    for url in (DISCOVER_WATCHLIST, METADATA_WATCHLIST):
        try:
            with client(timeout=20) as c:
                r = c.get(url, headers=_headers(token), params=params)
                r.raise_for_status()
                data = r.json() or {}
        except Exception as exc:
            log.warning("watchlist fetch via %s failed: %s", url, exc)
            continue
        container = data.get("MediaContainer") or {}
        metas = container.get("Metadata") or []
        for md in metas:
            items.append({
                "type": md.get("type"),  # movie | show
                "title": md.get("title"),
                "year": md.get("year"),
                "guids": _parse_guids(md.get("Guid")),
                "ratingKey": md.get("ratingKey"),
            })
        if items:
            break  # first endpoint that returns wins
    return items


def _already_owned(item: dict, session: Session) -> bool:
    """True if a movie/series with a matching tmdb/tvdb/imdb id (or title+year)
    already exists in the library."""
    guids = item.get("guids") or {}
    title = (item.get("title") or "").lower().strip()
    year = item.get("year")
    if item.get("type") == "movie":
        rows = session.exec(select(Movie)).all()
        for m in rows:
            prov = ((m.meta or {}).get("providers") or {})
            if guids.get("tmdb") and str((prov.get("tmdb") or {}).get("id")) == str(guids["tmdb"]):
                return True
            if guids.get("imdb") and (prov.get("imdb") or {}).get("id") == guids["imdb"]:
                return True
            if m.title.lower().strip() == title and (not year or m.year == year):
                return True
    else:
        rows = session.exec(select(Series)).all()
        for s in rows:
            prov = ((s.meta or {}).get("providers") or {})
            if guids.get("tvdb") and str((prov.get("tvdb") or {}).get("id")) == str(guids["tvdb"]):
                return True
            if guids.get("tmdb") and str((prov.get("tmdb") or {}).get("id")) == str(guids["tmdb"]):
                return True
            if s.title.lower().strip() == title and (not year or s.year == year):
                return True
    return False


def sync_watchlist(session: Session) -> dict:
    """Aggregate every managed user's Plex watchlist; for each item not already
    owned, create a monitored shell + auto_grab. De-dupes across users."""
    global _LAST_SYNC
    if not settings.PLEX_TOKEN:
        msg = "PLEX_TOKEN not configured — watchlist sync skipped"
        _LAST_SYNC = {"items": [], "status": msg, "users": []}
        log.info(msg)
        return {"status": msg, "items": 0, "grabbed": 0}

    # owner + home users (each may expose its own token)
    tokens: list[tuple[str, str]] = [("owner", settings.PLEX_TOKEN)]
    user_titles = ["owner"]
    for u in _home_users():
        user_titles.append(u.get("title") or "?")
        if u.get("token"):
            tokens.append((u.get("title") or "user", u["token"]))

    # gather + de-dupe across users by a stable key
    seen: set[str] = set()
    merged: list[dict] = []
    for _name, tok in tokens:
        for item in _watchlist_for_token(tok):
            guids = item.get("guids") or {}
            key = (guids.get("tmdb") or guids.get("tvdb") or guids.get("imdb")
                   or f"{item.get('type')}:{(item.get('title') or '').lower()}:{item.get('year')}")
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)

    grabbed = 0
    cache_items = []
    for item in merged:
        owned = _already_owned(item, session)
        cache_items.append({**item, "owned": owned})
        if owned:
            continue
        try:
            grab = _grab_watchlist_item(item, session)
            if grab:
                grabbed += 1
        except Exception:
            log.exception("watchlist grab failed for %s", item.get("title"))

    log_event(kind="watchlist", title="watchlist sync",
              message=f"{len(merged)} item(s) across {len(tokens)} user(s); {grabbed} queued")
    _LAST_SYNC = {
        "items": cache_items,
        "status": f"synced {len(merged)} item(s) across {len(tokens)} account(s)",
        "users": user_titles,
    }
    return {"status": _LAST_SYNC["status"], "items": len(merged), "grabbed": grabbed}


def _grab_watchlist_item(item: dict, session: Session):
    """Create a monitored Movie/Series shell, then ENQUEUE it (source=watchlist) so
    the throttled dispatcher grabs it within the auto-download cap — instead of
    firing at qBit immediately."""
    guids = item.get("guids") or {}
    title = item.get("title") or ""
    year = item.get("year")
    if item.get("type") == "movie":
        save_path = orchestrator.build_save_path("movie", title=title, year=year)
        shell = Movie(title=title, year=year, folder_path=save_path, monitored=True,
                      has_file=False,
                      meta={"providers": {k: {"id": v} for k, v in guids.items()},
                            "source": "watchlist"})
        session.add(shell)
        session.commit()
        session.refresh(shell)
        query = f"{title} {year}" if year else title
        return download_queue.enqueue_item(
            session, "movie", item_id=shell.id, title=title, query=query,
            save_path=save_path, source="watchlist")
    else:
        # tv: create a series shell; enqueue season 1 as a starting point
        folder = orchestrator.build_save_path("tv", title=title, season=1)
        # series folder is one level up from the Season dir
        import os
        series_folder = os.path.dirname(folder)
        shell = Series(title=title, year=year, folder_path=series_folder, monitored=True,
                       meta={"providers": {k: {"id": v} for k, v in guids.items()},
                             "source": "watchlist"})
        session.add(shell)
        session.commit()
        session.refresh(shell)
        return download_queue.enqueue_item(
            session, "tv", item_id=shell.id, title=title, query=title,
            save_path=folder, source="watchlist", series=title, season=1)


def list_watchlist(session: Session) -> dict:
    """Cached view of the last sync (does not hit the network)."""
    return _LAST_SYNC
