"""qBittorrent WebUI client (synchronous).

Ported from the proven ``QBittorrentClient.js``. The only reliable way to add a
torrent in qBit v5 is the file:// workaround: write the ``.torrent`` bytes into
the save directory and pass ``urls=file://<path>`` with ``contentLayout=NoSubfolder``
so files land flat in ``savepath`` (no torrent-root dir), then clean up.
"""
from __future__ import annotations

import hashlib
import logging
import os
import uuid

from ..config import settings
from ..http import client

log = logging.getLogger("managarr.qbit")


def _bdecode(data: bytes, i: int):
    """Tiny bencode decoder -> (value, next_index). Enough to read a .torrent."""
    c = data[i:i + 1]
    if c == b"i":
        j = data.index(b"e", i)
        return int(data[i + 1:j]), j + 1
    if c == b"l":
        i += 1
        out = []
        while data[i:i + 1] != b"e":
            v, i = _bdecode(data, i)
            out.append(v)
        return out, i + 1
    if c == b"d":
        i += 1
        out = {}
        while data[i:i + 1] != b"e":
            k, i = _bdecode(data, i)
            v, i = _bdecode(data, i)
            out[k] = v
        return out, i + 1
    colon = data.index(b":", i)
    n = int(data[i:colon])
    start = colon + 1
    return data[start:start + n], start + n


def infohash_from_torrent(data: bytes) -> str | None:
    """Compute the v1 infohash (sha1 of the raw ``info`` dict) from .torrent bytes.

    Used to identify the just-added torrent in qBit when the indexer didn't give us
    an infohash, so we can set its queue priority. Returns lowercase hex or None."""
    try:
        if data[:1] != b"d":
            return None
        i = 1
        while data[i:i + 1] != b"e":
            key, i = _bdecode(data, i)
            v_start = i
            _val, i = _bdecode(data, i)
            if key == b"info":
                return hashlib.sha1(data[v_start:i]).hexdigest()
        return None
    except Exception as exc:
        log.warning("infohash_from_torrent failed: %s", exc)
        return None


class QBitClient:
    def __init__(self):
        self.base = f"http://{settings.QBT_HOST}:{settings.QBT_PORT}"
        self._sid: str | None = None

    # ── auth ──────────────────────────────────────────────────────────────────
    def login(self) -> str | None:
        try:
            with client(timeout=10) as c:
                r = c.post(
                    f"{self.base}/api/v2/auth/login",
                    data={"username": settings.QBT_USERNAME, "password": settings.QBT_PASSWORD},
                    headers={"Content-Type": "application/x-www-form-urlencoded",
                             "Referer": self.base},
                )
            sid = r.cookies.get("SID")
            if not sid:
                for raw in r.headers.get_list("set-cookie") if hasattr(r.headers, "get_list") else []:
                    if raw.startswith("SID="):
                        sid = raw.split(";")[0][4:]
                        break
            self._sid = sid or None
            if not self._sid:
                log.warning("qBittorrent login returned no SID")
            return self._sid
        except Exception as exc:
            log.warning("qBittorrent login failed: %s", exc)
            return None

    def _cookie_header(self) -> dict:
        # Try to obtain a SID, but qBittorrent may auto-authenticate this client
        # via WebUI AuthSubnetWhitelist (the cluster pod network), in which case
        # no SID is needed — return an empty header and let the request proceed.
        if not self._sid:
            self.login()
        return {"Cookie": f"SID={self._sid}"} if self._sid else {}

    # ── add ───────────────────────────────────────────────────────────────────
    @staticmethod
    def _add_succeeded(r) -> bool:
        """qBit's /torrents/add success signal across versions: old builds reply the
        literal ``Ok.``; v5 replies JSON {success_count, pending_count, failure_count,
        added_torrent_ids}. Treat success_count+pending_count>0 (no failures) as added."""
        text = (r.text or "").strip()
        if text == "Ok.":
            return True
        # qBit v5.x replies 409 "Conflict" when the torrent is already in the
        # session — it IS present (verified: count rises, torrent lands at 100%),
        # so this is the desired end state, NOT a failure. Without treating it as
        # success, grab() raised "rejected" -> no Grab row -> the importer (which
        # matches completed torrents to open Grabs by infohash) never imported it,
        # so the download seeded forever but never reached the library/Plex.
        if r.status_code == 409:
            return True
        if text.lower().startswith("fail"):
            return False
        try:
            j = r.json()
            return (j.get("failure_count", 0) == 0
                    and (j.get("success_count", 0) + j.get("pending_count", 0)) > 0)
        except Exception:
            return r.status_code in (200, 202) and text == ""

    def add_torrent(self, torrent_bytes: bytes, save_path: str, category: str,
                    filename: str, ratio: float | str | None = None) -> bool:
        # No hard auth requirement: under AuthSubnetWhitelist the add still works
        # without a SID. _cookie_header() supplies a SID only if we got one.
        #
        # Upload the .torrent BYTES directly (multipart) rather than the old
        # urls=file:// workaround: qBit v5 reads file:// asynchronously and returns
        # pending, so deleting the temp file (or odd path chars like an apostrophe)
        # raced/failed the add. Multipart is synchronous and path-safe.
        auth_hdr = self._cookie_header()
        os.makedirs(save_path, exist_ok=True)
        safe_name = filename or f"_managarr_{uuid.uuid4().hex[:8]}.torrent"
        if not safe_name.endswith(".torrent"):
            safe_name += ".torrent"
        data = {"savepath": save_path, "contentLayout": "NoSubfolder"}
        if category:
            data["category"] = category
        if ratio not in (None, ""):
            data["ratioLimit"] = str(ratio)
        try:
            with client(timeout=30) as c:
                r = c.post(
                    f"{self.base}/api/v2/torrents/add",
                    files={"torrents": (safe_name, torrent_bytes, "application/x-bittorrent")},
                    data=data,
                    headers=auth_hdr,   # httpx sets the multipart Content-Type/boundary
                )
            ok = self._add_succeeded(r)
            if not ok:
                log.warning("qBittorrent add returned: %s (status %s)", (r.text or "")[:160], r.status_code)
            return ok
        except Exception as exc:
            log.error("qBittorrent add_torrent failed: %s", exc)
            return False

    # ── query ─────────────────────────────────────────────────────────────────
    def torrents(self, category: str | None = None, *, filter: str | None = None,
                 sort: str | None = None, reverse: bool = False,
                 limit: int | None = None, offset: int | None = None) -> list[dict]:
        """List torrents. qBit does server-side filter/sort/limit/offset, so pass
        those through (e.g. filter='completed', sort='completion_on', reverse=True,
        limit=50) to page large categories without pulling the whole list."""
        hdr = self._cookie_header()
        params: dict = {}
        if category:
            params["category"] = category
        if filter:
            params["filter"] = filter
        if sort:
            params["sort"] = sort
        if reverse:
            params["reverse"] = "true"
        if limit is not None:
            params["limit"] = int(limit)
        if offset is not None:
            params["offset"] = int(offset)
        try:
            with client(timeout=15) as c:
                r = c.get(f"{self.base}/api/v2/torrents/info", params=params, headers=hdr)
                r.raise_for_status()
                return r.json() or []
        except Exception as exc:
            log.warning("qBittorrent torrents() failed: %s", exc)
            return []

    def app_version(self) -> str | None:
        """Reachability/health probe. Works under AuthSubnetWhitelist (no SID)."""
        hdr = self._cookie_header()
        try:
            with client(timeout=10) as c:
                r = c.get(f"{self.base}/api/v2/app/version", headers=hdr)
                if r.status_code == 200 and r.text.strip():
                    return r.text.strip()
        except Exception as exc:
            log.warning("qBittorrent app_version() failed: %s", exc)
        return None

    def torrent_files(self, hash: str) -> list[dict]:
        hdr = self._cookie_header()
        try:
            with client(timeout=15) as c:
                r = c.get(f"{self.base}/api/v2/torrents/files", params={"hash": hash}, headers=hdr)
                r.raise_for_status()
                return r.json() or []
        except Exception as exc:
            log.warning("qBittorrent torrent_files() failed: %s", exc)
            return []

    def _post(self, path: str, data: dict) -> bool:
        hdr = {**self._cookie_header(), "Content-Type": "application/x-www-form-urlencoded"}
        try:
            with client(timeout=30) as c:
                r = c.post(f"{self.base}{path}", data=data, headers=hdr)
                return r.status_code == 200
        except Exception as exc:
            log.warning("qBittorrent POST %s failed: %s", path, exc)
            return False

    def set_location(self, hash: str, location: str) -> bool:
        """Move a torrent's files to `location` and keep seeding from there."""
        return self._post("/api/v2/torrents/setLocation", {"hashes": hash, "location": location})

    def resume(self, hash: str) -> bool:
        """Start/resume a torrent. qBit v5.0 renamed this endpoint
        /torrents/resume -> /torrents/start; the old path no longer dispatches
        (qBit logs "No such method TorrentsController::resumeAction()" and the
        torrent never leaves its stopped/checking state). Prefer the v5 verb and
        fall back to the legacy one for pre-5.0 servers."""
        return (self._post("/api/v2/torrents/start", {"hashes": hash})
                or self._post("/api/v2/torrents/resume", {"hashes": hash}))

    def delete_torrent(self, hashes: str, delete_files: bool = False) -> bool:
        """Remove a torrent from qBit. ``delete_files=False`` (default) leaves any
        downloaded data on disk — used when abandoning an in-flight auto-download
        whose library item was deleted."""
        return self._post("/api/v2/torrents/delete",
                          {"hashes": hashes, "deleteFiles": "true" if delete_files else "false"})

    def recheck(self, hash: str) -> bool:
        return self._post("/api/v2/torrents/recheck", {"hashes": hash})

    # ── queue priority ──────────────────────────────────────────────────────────
    def top_priority(self, hashes: str) -> bool:
        return self._post("/api/v2/torrents/topPrio", {"hashes": hashes})

    def bottom_priority(self, hashes: str) -> bool:
        return self._post("/api/v2/torrents/bottomPrio", {"hashes": hashes})

    def set_force_start(self, hashes: str, value: bool = True) -> bool:
        return self._post("/api/v2/torrents/setForceStart",
                          {"hashes": hashes, "value": "true" if value else "false"})

    def apply_queue_priority(self, hashes: str, mode: str) -> bool:
        """Apply the operator's download-priority preference to a torrent.

        ``top``/``bottom`` move it within qBit's download queue (needs queueing
        enabled); ``force`` force-starts it, bypassing the queue/limits entirely.
        Anything else (``none``/``normal``) is a no-op — leave qBit's default."""
        mode = (mode or "").strip().lower()
        if not hashes or mode in ("", "none", "normal"):
            return False
        if mode == "top":
            return self.top_priority(hashes)
        if mode == "bottom":
            return self.bottom_priority(hashes)
        if mode in ("force", "force_start", "forcestart"):
            return self.set_force_start(hashes, True)
        log.warning("unknown queue priority mode %r", mode)
        return False

    def find_by_path(self, folder: str) -> dict | None:
        """Return the torrent whose save_path/content_path matches `folder`."""
        folder = folder.rstrip("/")
        for t in self.torrents():
            for key in ("save_path", "content_path"):
                p = (t.get(key) or "").rstrip("/")
                if p == folder or p == os.path.dirname(folder) or os.path.dirname(p) == folder:
                    return t
        return None
