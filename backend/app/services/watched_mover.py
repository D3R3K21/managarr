"""Watched-movie mover (ported from the standalone movie-mover, now Managarr-native).

Movies download into the **New Movies** library and seed there. Once a
movie is marked watched in Plex (``viewCount > 0``), this relocates it to the
permanent **Movies** library:

1. qBittorrent ``setLocation`` moves the files New Movies -> Movies and keeps
   seeding from the new path (no separate copy; fallback to a Managarr copy+replace
   if there's no torrent for the folder).
2. wait for the move to finish, then update Managarr's DB,
3. drop the item from the Plex "New Movies" section and **refresh both Plex
   sections** so the movie re-appears under Movies.
"""
from __future__ import annotations

import logging
import os
import shutil
import time

from sqlmodel import Session, select

from ..config import settings
from ..http import client
from ..models import Movie
from ..notify import notify
from ..pipeline.qbit import QBitClient

log = logging.getLogger("managarr.watched_mover")

NEW_LIB = os.getenv("NEW_MOVIES_LIBRARY", "New Movies")
MOVIES_LIB = os.getenv("MOVIES_LIBRARY", "Movies")
MOVE_TIMEOUT = int(os.getenv("MOVE_TIMEOUT_SEC", "900"))


# ── Plex (local server) ───────────────────────────────────────────────────────
def _plex(path: str, method: str = "GET", **params):
    params["X-Plex-Token"] = settings.PLEX_TOKEN
    url = f"{settings.PLEX_URL.rstrip('/')}{path}"
    with client(timeout=30) as c:
        r = c.request(method, url, params=params, headers={"Accept": "application/json"})
        r.raise_for_status()
        if method == "GET" and r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return None


def _sections() -> dict[str, str]:
    data = _plex("/library/sections")
    return {d["title"]: d["key"] for d in data["MediaContainer"].get("Directory", [])}


def _watched(section_key: str) -> list[dict]:
    """Movies in this section with viewCount>0 (Plex sets this near completion)."""
    data = _plex(f"/library/sections/{section_key}/all", type=1)
    return [m for m in data["MediaContainer"].get("Metadata", []) if m.get("viewCount", 0) > 0]


def _item_folder(meta: dict) -> str | None:
    """The movie's folder from its Plex file part, normalised to a direct child of
    NEW_MOVIES_PATH (so it matches qBit's save path)."""
    for media in meta.get("Media", []):
        for part in media.get("Part", []):
            f = part.get("file")
            if f and f.startswith(settings.NEW_MOVIES_PATH):
                rel = os.path.relpath(f, settings.NEW_MOVIES_PATH)
                top = rel.split(os.sep)[0]
                return os.path.join(settings.NEW_MOVIES_PATH, top)
    return None


def _plex_delete(rating_key: str) -> None:
    try:
        _plex(f"/library/metadata/{rating_key}", method="DELETE")
    except Exception as exc:
        log.warning("plex delete %s failed: %s", rating_key, exc)


def _plex_refresh(section_key: str) -> None:
    try:
        _plex(f"/library/sections/{section_key}/refresh")
    except Exception as exc:
        log.warning("plex refresh %s failed: %s", section_key, exc)


# ── move helpers ──────────────────────────────────────────────────────────────
def _tree_size(p: str) -> int:
    total = 0
    for root, _d, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _managarr_move(src: str, dest_root: str) -> str:
    """Cross-PVC move via staging + atomic replace (used when there's no torrent)."""
    dest = os.path.join(dest_root, os.path.basename(src))
    staging = os.path.join(dest_root, f".{os.path.basename(src)}.partial")
    if os.path.exists(staging):
        shutil.rmtree(staging, ignore_errors=True)
    if os.path.exists(dest):
        if _tree_size(dest) >= _tree_size(src):
            shutil.rmtree(src, ignore_errors=True)
            return dest
        shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(src, staging)
    os.replace(staging, dest)
    shutil.rmtree(src, ignore_errors=True)
    return dest


def _wait_for_move(qbt: QBitClient, hash: str, dest_folder: str) -> bool:
    """Wait until qBit has actually finished moving the torrent's data.

    qBit flips save_path to the new location the moment setLocation is accepted,
    while the data is still copying (state == "moving") — so save_path alone is
    not confirmation, and neither is the dest folder being non-empty (it may hold
    a partial copy, or pre-existing files for a shared collection folder). Only
    save_path under dest AND state past "moving" counts."""
    deadline = time.time() + MOVE_TIMEOUT
    while time.time() < deadline:
        for t in qbt.torrents():
            if (t.get("hash") or "").lower() == hash.lower():
                sp = (t.get("save_path") or "").rstrip("/")
                if sp == dest_folder.rstrip("/") and t.get("state") != "moving":
                    return True
                break
        time.sleep(10)
    return False


def _sweep_leftovers(src: str, dest: str) -> None:
    """qBit's setLocation moves only torrent-tracked files; anything Managarr wrote
    next to them (managarr.json / movie.nfo / poster.jpg) stays behind in src. Carry
    those over so the metadata travels with the movie, and drop the emptied folder
    so /new-movies doesn't accumulate litter."""
    if not os.path.isdir(src) or not os.path.isdir(dest):
        return
    try:
        for root, _dirs, files in os.walk(src, topdown=False):
            rel = os.path.relpath(root, src)
            for f in files:
                s = os.path.join(root, f)
                d = os.path.join(dest, f) if rel == "." else os.path.join(dest, rel, f)
                if os.path.exists(d):
                    os.remove(s)
                    continue
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.move(s, d)
            if not os.listdir(root):
                os.rmdir(root)
    except OSError:
        log.warning("leftover sweep failed for %s", src, exc_info=True)


def _qbt_owns(qbt: QBitClient, folder: str) -> bool:
    """True if any torrent's save path is at/under `folder` — i.e. qBit still owns
    that data (e.g. a cross-PVC setLocation move from a previous run is still in
    flight). Deleting or replacing the folder while that's true loses files."""
    folder = folder.rstrip("/")
    for t in qbt.torrents():
        sp = (t.get("save_path") or "").rstrip("/")
        if sp and (sp == folder or sp.startswith(folder + "/")):
            return True
    return False


# ── main ──────────────────────────────────────────────────────────────────────
def move_watched_movies(session: Session) -> dict:
    if not settings.PLEX_TOKEN:
        return {"error": "PLEX_TOKEN not set", "moved": 0}
    try:
        sections = _sections()
    except Exception as exc:
        log.error("cannot reach Plex: %s", exc)
        return {"error": f"plex unreachable: {exc}", "moved": 0}

    new_key = sections.get(NEW_LIB)
    movies_key = sections.get(MOVIES_LIB)
    if not new_key:
        return {"error": f"Plex library '{NEW_LIB}' not found", "moved": 0}

    watched = _watched(new_key)
    if not watched:
        return {"moved": 0, "checked": 0}

    qbt = QBitClient()
    moved = 0
    handled_srcs: set[str] = set()
    for meta in watched:
        title = meta.get("title", "?")
        src = _item_folder(meta)
        if not src or not os.path.isdir(src):
            log.info("watched '%s' has no New Movies folder — skipping", title)
            continue
        if src in handled_srcs:
            # Several Plex items can share one folder (a collection pack): a second
            # move racing the first is how files get deleted mid-copy. One move per
            # folder per run; Plex's section refresh drops the leftover items.
            log.info("'%s' shares already-handled folder %s — skipping this run", title, src)
            continue
        dest = os.path.join(settings.MOVIES_PATH, os.path.basename(src))
        try:
            tor = qbt.find_by_path(src)
            if tor:
                log.info("relocating '%s' via qBit setLocation: %s -> %s", title, src, dest)
                qbt.set_location(tor["hash"], dest)
                qbt.resume(tor["hash"])
                if not _wait_for_move(qbt, tor["hash"], dest):
                    # qBit still owns the data; deleting the Plex item now would
                    # remove source files out from under the copy. Leave the item
                    # watched — the next run reconciles once the move lands.
                    log.warning("qBit move for '%s' didn't confirm within timeout — deferring cleanup", title)
                    handled_srcs.add(src)
                    continue
                _sweep_leftovers(src, dest)
            elif _qbt_owns(qbt, dest) or _qbt_owns(qbt, src):
                # No torrent matched src, but one still claims src or dest — a
                # deferred move from an earlier run is likely mid-flight. Hands off.
                log.info("qBit still owns data for '%s' — deferring", title)
                handled_srcs.add(src)
                continue
            else:
                log.info("no torrent for '%s' — Managarr move %s -> %s", title, src, dest)
                _managarr_move(src, settings.MOVIES_PATH)
            handled_srcs.add(src)

            # update Managarr DB if we track this movie
            row = session.exec(select(Movie).where(Movie.folder_path == src)).first()
            if row:
                row.folder_path = dest
                session.add(row)
                session.commit()

            # drop from New Movies + rescan both Plex sections
            if meta.get("ratingKey"):
                _plex_delete(meta["ratingKey"])
            _plex_refresh(new_key)
            if movies_key:
                _plex_refresh(movies_key)
            notify(f"Moved to Movies: {title}", f"{src} -> {dest}", kind="moved", media_kind="movie")
            moved += 1
        except Exception as exc:
            log.exception("move failed for '%s'", title)
            notify(f"Move failed: {title}", str(exc), kind="error", media_kind="movie", priority="high")

    return {"moved": moved, "checked": len(watched)}
