"""Indexer abstraction.

All concrete indexers return the normalized Release dict (per CONTRACT) and
fetch the raw ``.torrent`` bytes for a chosen release. Tracker traffic egresses
through the gluetun proxy (``app.http.proxied_client``).
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

# Normalized Release dict (per CONTRACT):
#   {title, indexer, download_url, detail_url, infohash, size_bytes,
#    seeders, leechers, categories, publish_date}


def release(
    *,
    title: str,
    indexer: str,
    download_url: str,
    detail_url: str | None = None,
    infohash: str | None = None,
    size_bytes: int | None = None,
    seeders: int = 0,
    leechers: int = 0,
    categories: list[str] | None = None,
    publish_date: str | None = None,
    language: str | None = None,
) -> dict:
    """Construct a normalized Release dict (keeps callers consistent)."""
    return {
        "title": title,
        "indexer": indexer,
        "download_url": download_url,
        "detail_url": detail_url,
        "infohash": infohash,
        "language": language,
        "size_bytes": size_bytes,
        "seeders": int(seeders or 0),
        "leechers": int(leechers or 0),
        "categories": categories or [],
        "publish_date": publish_date,
    }


_SIZE_RE = re.compile(r"([\d.]+)\s*([KMGT]?I?B)", re.I)
_UNITS = {
    "B": 1, "KB": 1024, "KIB": 1024, "MB": 1024**2, "MIB": 1024**2,
    "GB": 1024**3, "GIB": 1024**3, "TB": 1024**4, "TIB": 1024**4,
}


def parse_size(text: Any) -> int | None:
    """Parse a human size string ('1.4 GB', '350 MiB') to bytes."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return int(text)
    m = _SIZE_RE.search(str(text))
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2).upper().replace("I", "I")
    return int(val * _UNITS.get(unit, 1))


class Indexer(ABC):
    name: str = "indexer"
    kind: str = "generic"

    @abstractmethod
    def search(self, query: str, media_kind: str, **kw) -> list[dict]:
        """Return a list of normalized Release dicts."""
        raise NotImplementedError

    @abstractmethod
    def fetch_torrent(self, release: dict) -> bytes:
        """Download the raw .torrent bytes for a chosen release. Raises on failure."""
        raise NotImplementedError
