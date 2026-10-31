"""Client for Radarr, the source we migrate movie franchises from.

Radarr runs in the same ``media`` namespace as Managarr, reached by short service
name over the LAN — querying it is free (no TMDB rate limits). ``/api/v3/collection``
gives every franchise with its full part list (owned + not), posters and overviews;
``/api/v3/movie`` tells us which parts we own (by TMDB id).

Audiobook *series* don't need an external call: AudiobookShelf's ``metadata.json``
series+sequence is already ingested into each ``Audiobook.meta["scanned_series"]``
during the library scan, so we group those purely from our own DB.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from ..config import settings

log = logging.getLogger("managarr.external")

_TIMEOUT = httpx.Timeout(30.0, connect=8.0)


# ── Radarr ────────────────────────────────────────────────────────────────────
def radarr_configured() -> bool:
    return bool(settings.RADARR_URL and settings.RADARR_API_KEY)


def _radarr_get(path: str) -> Any:
    url = settings.RADARR_URL.rstrip("/") + path
    headers = {"X-Api-Key": settings.RADARR_API_KEY}
    with httpx.Client(timeout=_TIMEOUT) as c:
        r = c.get(url, headers=headers)
        r.raise_for_status()
        return r.json()


def radarr_collections() -> list[dict]:
    """All Radarr collections (franchises) with their full part list + images."""
    try:
        data = _radarr_get("/api/v3/collection")
        return data if isinstance(data, list) else []
    except Exception as exc:
        log.warning("radarr_collections failed: %s", exc)
        return []


def radarr_movies() -> list[dict]:
    """All Radarr movies (we use ``tmdbId`` + ``collection`` + ``path``)."""
    try:
        data = _radarr_get("/api/v3/movie")
        return data if isinstance(data, list) else []
    except Exception as exc:
        log.warning("radarr_movies failed: %s", exc)
        return []


def radarr_poster(images: Optional[list]) -> Optional[str]:
    """Pull the poster URL out of a Radarr ``images`` array."""
    for img in images or []:
        if img.get("coverType") == "poster":
            return img.get("remoteUrl") or img.get("url")
    return None
