"""MyAnonamouse indexer (audiobooks).

Auth uses a MAM **dynamic-seedbox** session (``mam_id``), created from the VPN-side
browser so it's bound to the gluetun exit ASN. The token comes from
``settings.MAM_COOKIE`` (preferred) or, as a fallback, Mousehole's ``/state``
currentCookie. All MAM traffic egresses via the gluetun proxy so the source ASN
matches the session.

- ``_refresh_seedbox`` periodically pings ``/json/dynamicSeedbox.php`` to keep the
  session valid as the VPN IP rotates within the allowed ASN (throttled; MAM
  rate-limits it).
- search hits ``/tor/js/loadSearchJSONbasic.php`` (main_cat 13 = Audiobooks),
  2 s minimum spacing between requests (anti-ban).
- ``fetch_torrent`` downloads ``/tor/download.php?tid=`` and validates the bytes
  start with ``b"d"`` (bencoded dict).
"""
from __future__ import annotations

import logging
import threading
import time

from ..config import settings
from ..http import client, proxied_client
from .base import Indexer, parse_size, release

log = logging.getLogger("managarr.indexers.mam")

MAM_BASE = "https://www.myanonamouse.net"
MAM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; managarr)",
    "Accept": "application/json",
    "Referer": "https://www.myanonamouse.net/",
}


# MAM's own language ids, for the server-side filter.
LANGUAGE_IDS = {"ENG": 1, "SPA": 4, "GER": 5, "FRE": 6, "ITA": 7, "POR": 8, "RUS": 9, "JPN": 17}
DEFAULT_LANGUAGES = ["ENG"]


class MAMIndexer(Indexer):
    name = "MyAnonamouse"
    kind = "mam"

    _lock = threading.Lock()
    _last_request = 0.0
    _min_delay = 2.0
    _last_seedbox = 0.0
    _seedbox_interval = 600.0  # refresh the dynamic-seedbox session at most every 10 min

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.main_cat = str(self.config.get("main_cat", "13"))
        # Which languages to accept. MAM both filters server-side (`tor[browse_lang][]`, numeric
        # ids) and labels every result (`lang_code`), so we use both: the request narrows the
        # search, the check below throws away anything that slips past. Set `languages` to an empty
        # list to accept every language.
        langs = self.config.get("languages", DEFAULT_LANGUAGES)
        self.languages = [str(x).upper() for x in langs] if langs else []
        self.browse_lang = [LANGUAGE_IDS[x] for x in self.languages if x in LANGUAGE_IDS]

    # ── helpers ───────────────────────────────────────────────────────────────
    def _rate_limit(self) -> None:
        with MAMIndexer._lock:
            elapsed = time.time() - MAMIndexer._last_request
            if elapsed < self._min_delay:
                time.sleep(self._min_delay - elapsed)
            MAMIndexer._last_request = time.time()

    @staticmethod
    def _strip_prefix(v: str) -> str:
        low = v.lower()
        for pfx in ("mam_id=", "mbsc="):
            if low.startswith(pfx):
                return v[len(pfx):]
        return v

    def _seed(self) -> str:
        """The initial mam_id seed (indexer config or MAM_COOKIE), prefix-stripped."""
        return self._strip_prefix((self.config.get("cookie") or settings.MAM_COOKIE or "").strip())

    def _persist_rotation(self, resp) -> None:
        """MAM rotates the mam_id on keep-alive/seedbox calls and invalidates the
        prior one. Capture the new Set-Cookie value and persist it (tied to the
        active seed) so later requests use the live token and it survives restarts."""
        try:
            if resp.status_code >= 400:
                return
            new = resp.cookies.get("mam_id")
        except Exception:
            new = None
        if new and len(new) > 20:
            from ..settings_store import set_setting
            set_setting("mam_id_live", new)
            set_setting("mam_id_seed", self._seed())
            log.info("MAM mam_id rotated; persisted new token")

    def _refresh_seedbox(self, cookie: str) -> None:
        """Keep the dynamic-seedbox session alive as the VPN IP rotates within the
        allowed ASN, persisting the rotated mam_id. Throttled (MAM rate-limits)."""
        with MAMIndexer._lock:
            if time.time() - MAMIndexer._last_seedbox < self._seedbox_interval:
                return
            MAMIndexer._last_seedbox = time.time()
        try:
            with proxied_client(timeout=15) as c:
                r = c.get("https://t.myanonamouse.net/json/dynamicSeedbox.php",
                          headers={**MAM_HEADERS, "Cookie": f"mam_id={cookie}"})
                self._persist_rotation(r)
        except Exception as exc:
            log.debug("MAM seedbox refresh failed (non-fatal): %s", exc)

    def _cookie(self) -> str:
        """Current mam_id: the live (rotated) token if it derives from the active
        seed; else the seed itself; else Mousehole's currentCookie."""
        from ..settings_store import get_setting
        seed = self._seed()
        if seed:
            live = (get_setting("mam_id_live") or "").strip()
            origin = (get_setting("mam_id_seed") or "").strip()
            if live and origin == seed:
                return self._strip_prefix(live)
            return seed
        url = (settings.MOUSEHOLE_URL or "").rstrip("/")
        if not url:
            return ""
        try:
            with client(timeout=5) as c:
                r = c.get(f"{url}/state")
                return (r.json() or {}).get("currentCookie", "") or ""
        except Exception as exc:
            log.warning("mousehole /state unavailable: %s", exc)
            return ""

    # ── search ────────────────────────────────────────────────────────────────
    def search(self, query: str, media_kind: str, **kw) -> list[dict]:
        if media_kind not in ("audiobook", "book", ""):
            return []
        cookie = self._cookie()
        if not cookie:
            log.warning("no MAM session cookie (set MAM_COOKIE or configure Mousehole)")
            return []
        self._refresh_seedbox(cookie)
        cookie = self._cookie()  # re-read: the seedbox refresh may have rotated the token

        author = kw.get("author", "")
        search_text = " ".join(filter(None, [query, author]))
        params: dict = {
            "tor[text]": search_text,
            "tor[searchType]": "all",
            "tor[searchIn]": "torrents",
            "tor[main_cat][]": self.main_cat,
            "json": "noredirect",
            "Qs": search_text,
        }
        if self.browse_lang:
            params["tor[browse_lang][]"] = self.browse_lang

        self._rate_limit()
        try:
            with proxied_client(timeout=20) as c:
                r = c.get(
                    f"{MAM_BASE}/tor/js/loadSearchJSONbasic.php",
                    params=params,
                    headers={**MAM_HEADERS, "Cookie": f"mam_id={cookie}"},
                )
                self._persist_rotation(r)
                r.raise_for_status()
                data = r.json()
        except Exception as exc:
            log.warning("MAM search failed: %s", exc)
            return []

        torrents = (data or {}).get("data") or []
        if not isinstance(torrents, list):
            return []

        out = []
        dropped = 0
        for t in torrents:
            tid = t.get("id")
            if tid is None:
                continue
            lang = (t.get("lang_code") or "").strip().upper() or None
            # A Spanish edition of an English book matches the title perfectly, so without this it
            # can be grabbed instead of the one that was wanted.
            if self.languages and lang and lang not in self.languages:
                dropped += 1
                continue
            out.append(release(
                title=t.get("name") or t.get("title") or "",
                indexer=self.name,
                download_url=f"{MAM_BASE}/tor/download.php?tid={tid}",
                detail_url=f"{MAM_BASE}/t/{tid}",
                size_bytes=parse_size(t.get("size")),
                seeders=int(t.get("seeders") or 0),
                leechers=int(t.get("leechers") or 0),
                categories=["audiobook"],
                publish_date=t.get("added") or None,
                language=lang,
            ))
        if dropped:
            log.info("MAM: dropped %d result(s) not in %s", dropped, self.languages)
        out.sort(key=lambda r: -r["seeders"])
        return out

    # ── fetch ─────────────────────────────────────────────────────────────────
    def fetch_torrent(self, release: dict) -> bytes:
        url = release.get("download_url")
        if not url:
            raise ValueError("release has no download_url")
        cookie = self._cookie()
        if not cookie:
            raise RuntimeError("no MAM session cookie for torrent download")
        self._refresh_seedbox(cookie)
        cookie = self._cookie()  # re-read after possible rotation
        self._rate_limit()
        with proxied_client(timeout=20) as c:
            r = c.get(url, headers={**MAM_HEADERS, "Cookie": f"mam_id={cookie}"})
            self._persist_rotation(r)
            r.raise_for_status()
            data = r.content
        if not data or data[0:1] != b"d":
            raise ValueError("downloaded file is not a valid torrent (bad format)")
        return data
