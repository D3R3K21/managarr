"""IPTorrents indexer (movies / TV).

Auth is the private-tracker login cookie (``uid=...; pass=...``) from
``settings.IPT_COOKIE`` (sourced from IP_TORRENTS_TRACKER_CREDS). All traffic
egresses via the gluetun proxy.

IPT does not expose a stable public JSON API, so this is a best-effort HTML
scrape of the ``/t`` browse/search page. The selectors below match the classic
IPT table layout (``table.torrents`` rows); they are intentionally defensive and
documented so they can be adjusted if the site markup drifts.

Verified row layout (``table#torrents`` on iptorrents.com, 2026) — note cells are
NOT closed with ``</td>``:
  <tr>
    <td class="i ..."><a href="?<cat>"><img .../></a>        # category icon
    <td class="al"><a class=" hv" href="/t/<id>">NAME</a>... # title + .sub meta
    <td> ... bookmark ...
    <td><a href="/download.php/<id>/<name>.torrent">         # download
    <td><a href="/t/<id>?...#startcomments">N                # comment count
    <td>1.45 GB <td>72 <td>40 <td>3 </tr>                    # size, snatched, seeders, leechers
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote

from ..config import settings
from ..http import proxied_client
from .base import Indexer, parse_size, release

log = logging.getLogger("managarr.indexers.iptorrents")

IPT_BASE = "https://iptorrents.com"
IPT_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; managarr)"}

_TABLE_RE = re.compile(r'<table id="torrents".*?</table>', re.I | re.S)
_DOWNLOAD_RE = re.compile(r'href="(/download\.php/(\d+)/[^"]+?\.torrent)"', re.I)
_TITLE_RE = re.compile(r'<td class="al">\s*<a[^>]*href="/t/(\d+)"[^>]*>([^<]+)</a>', re.I)
# size, snatched, seeders, leechers — trailing unclosed <td> cells before </tr>
_STATS_RE = re.compile(
    r'<td[^>]*>\s*([\d.]+\s*[KMGTP]i?B)\s*'
    r'<td[^>]*>\s*([\d,]+)\s*'
    r'<td[^>]*>\s*([\d,]+)\s*'
    r'<td[^>]*>\s*([\d,]+)\s*</tr>',
    re.I | re.S,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip(html: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub("", html)).strip()


def _dl_url(path: str) -> str:
    # The /download.php/<id>/<name>.torrent path contains spaces — encode them.
    return IPT_BASE + quote(path, safe="/?=&")


class IPTorrentsIndexer(Indexer):
    name = "IPTorrents"
    kind = "iptorrents"

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.cookie = (self.config.get("cookie") or settings.IPT_COOKIE or "").strip()

    def _client(self):
        return proxied_client(timeout=25)

    # ── search ────────────────────────────────────────────────────────────────
    def search(self, query: str, media_kind: str, **kw) -> list[dict]:
        if not self.cookie:
            log.warning("IPTorrents cookie not configured — skipping")
            return []

        params = {"q": query, "qf": ""}
        try:
            with self._client() as c:
                r = c.get(
                    f"{IPT_BASE}/t",
                    params=params,
                    headers={**IPT_HEADERS, "Cookie": self.cookie},
                )
                r.raise_for_status()
                html = r.text
        except Exception as exc:
            log.warning("IPTorrents search failed: %s", exc)
            return []

        return self._parse(html, media_kind)

    def _parse(self, html: str, media_kind: str) -> list[dict]:
        """Parse the main results table (#torrents), one release per <tr>."""
        out: list[dict] = []
        tbl = _TABLE_RE.search(html)
        block = tbl.group(0) if tbl else html
        rows = re.split(r"<tr\b", block, flags=re.I)
        for row in rows:
            dl = _DOWNLOAD_RE.search(row)
            if not dl:
                continue
            download_path, tid = dl.group(1), dl.group(2)

            tm = _TITLE_RE.search(row)
            title = _strip(tm.group(2)) if tm else None
            if not title:
                fn = re.search(r'/download\.php/\d+/([^"]+)\.torrent', row)
                title = (fn.group(1).replace(".", " ").replace("_", " ").strip()
                         if fn else f"ipt-{tid}")

            size = seeders = leechers = None
            st = _STATS_RE.search(row)
            if st:
                size = parse_size(st.group(1))
                seeders = int(st.group(3).replace(",", ""))
                leechers = int(st.group(4).replace(",", ""))
            else:
                sm = re.search(r"([\d.]+\s*[KMGTP]i?B)", row)
                size = parse_size(sm.group(1)) if sm else None
                seeders = leechers = 0

            out.append(release(
                title=title,
                indexer=self.name,
                download_url=_dl_url(download_path),
                detail_url=f"{IPT_BASE}/t/{tid}",
                size_bytes=size,
                seeders=seeders or 0,
                leechers=leechers or 0,
                categories=[media_kind] if media_kind else [],
            ))
        out.sort(key=lambda r: -r["seeders"])
        return out

    # ── fetch ─────────────────────────────────────────────────────────────────
    def fetch_torrent(self, release: dict) -> bytes:
        url = release.get("download_url")
        if not url:
            raise ValueError("release has no download_url")
        if not self.cookie:
            raise RuntimeError("IPTorrents cookie not configured")
        with self._client() as c:
            r = c.get(url, headers={**IPT_HEADERS, "Cookie": self.cookie})
            r.raise_for_status()
            data = r.content
        if not data or data[0:1] != b"d":
            raise ValueError("downloaded file is not a valid torrent (bad format)")
        return data
