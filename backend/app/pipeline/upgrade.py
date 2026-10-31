"""Quality upgrades — replace an owned (poor) release with a better one.

Unlike a normal grab (which acquires a *missing* item), an upgrade targets an item
that already ``has_file``. The new release is downloaded into the item's EXISTING
folder; once it imports, the old video file(s) are removed so only the upgraded copy
remains (posters/nfo are left for the scanner/sidecar to refresh).

Flow (two-phase, because the download is async):
  start_movie_upgrade()  -> choose a strictly-better release, grab into folder_path,
                            stash ``meta["upgrade"] = {grab_id, old_files, ...}``.
  finalize_movie_upgrade() -> called by the importer after the upgrade grab imports;
                            deletes the recorded old files (with a hard safety guard
                            that a genuinely new video landed first).

"Better" = passes the quality profile AND has a higher quality score than the file
on disk, so a telesync never swaps for another telesync (and cam never beats it).
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime

from sqlmodel import Session

from ..models import Grab, HistoryEvent, Movie
from ..notify import notify
from .decision import _matches_any, _tokens
from .scanner import VIDEO_EXTS

log = logging.getLogger("managarr.upgrade")


# ── quality scoring (source tier + resolution) ────────────────────────────────
# Higher = better. Tokens are matched on whole-word boundaries (see decision._matches_any),
# so "ts" only matches standalone, never inside "Knigh*ts*".
_SOURCE_TIERS: list[tuple[list[str], int]] = [
    (["remux"], 100),
    (["bluray", "blu-ray", "bdrip", "brrip", "bdremux"], 90),
    (["web-dl", "webdl", "web"], 70),
    (["webrip"], 65),
    (["hdtv", "hdrip"], 50),
    (["dvdrip", "dvd"], 40),
    (["hdcam", "hd-cam"], 20),
    (["telesync", "ts"], 15),
    (["cam", "camrip", "hdts"], 10),
]
_RES_BONUS: list[tuple[list[str], int]] = [
    (["2160p", "4k", "uhd"], 8),
    (["1080p"], 4),
    (["720p"], 2),
    (["480p", "sd"], 0),
]


def quality_score(title: str) -> int:
    """A coarse quality score for a release/file name: source tier + resolution bonus.

    Returns the BEST matching source tier (titles can carry multiple words) plus a
    resolution bonus. 0 when nothing matches (unknown → treated as worst, so an
    unknown never displaces a known-good file)."""
    t = _tokens(title)
    source = max((pts for words, pts in _SOURCE_TIERS if _matches_any(t, words)), default=0)
    res = max((pts for words, pts in _RES_BONUS if _matches_any(t, words)), default=0)
    return source + res


def _video_files(folder: str) -> list[str]:
    """Absolute paths of the video files currently in ``folder`` (non-recursive)."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return [os.path.join(folder, n) for n in names
            if os.path.splitext(n)[1].lower() in VIDEO_EXTS
            and os.path.isfile(os.path.join(folder, n))]


def current_movie_quality(movie: Movie) -> int:
    """Quality score of the movie's current file, from the on-disk video filename
    (falls back to the meta_source/nfo name, e.g. 'Obsession (2026) TELESYNC.nfo')."""
    vids = _video_files(movie.folder_path)
    if vids:
        return max(quality_score(os.path.basename(v)) for v in vids)
    return quality_score((movie.meta or {}).get("meta_source", "") or movie.title)


# ── start / finalize ───────────────────────────────────────────────────────────
def pick_upgrade(movie: Movie, session: Session, *, release: dict | None = None,
                 force: bool = False) -> tuple[dict | None, str]:
    """Resolve the release to upgrade to. Returns (release|None, reason).

    ``release`` given  -> validate it (must be strictly better unless ``force``).
    otherwise          -> search indexers + decide() against the movie profile, then
                          require the winner to be strictly better than the current file."""
    from ..pipeline import orchestrator  # lazy: orchestrator imports decision/qbit

    current = current_movie_quality(movie)
    if release is None:
        query = f"{movie.title} {movie.year}" if movie.year else movie.title
        releases = orchestrator.search("movie", query)
        if not releases:
            return None, "no_results"
        # Among profile-passing releases pick the HIGHEST QUALITY (not the most-seeded —
        # this is an upgrade), tie-broken by seeders.
        cands, _profile = orchestrator.candidates(releases, "movie")
        # Same fuzzy-match guard as the acquisition path: don't upgrade a movie
        # with a release for a different, similarly-named title.
        from .decision import title_relevant
        cands = [r for r in cands if title_relevant(r.get("title", ""), movie.title)]
        if not cands:
            return None, "none_passed_profile"
        release = max(cands, key=lambda r: (quality_score(r.get("title", "")),
                                            int(r.get("seeders") or 0)))
    if not force and quality_score(release.get("title", "")) <= current:
        return None, "not_better"
    return release, "ok"


def start_movie_upgrade(movie: Movie, session: Session, *, release: dict | None = None,
                        monitor: bool = False, force: bool = False) -> dict:
    """Begin an upgrade. Picks a better release (or uses the supplied one), grabs it
    into the movie's existing folder, and records the old files to remove on import.

    When nothing qualifies and ``monitor`` is set, flags the movie to be retried by
    the scheduled ``upgrade_search`` action and returns ``{"status": "monitoring"}``."""
    from ..pipeline import orchestrator

    chosen, reason = pick_upgrade(movie, session, release=release, force=force)
    if not chosen:
        if monitor:
            meta = dict(movie.meta or {})
            meta["upgrade_wanted"] = True
            meta["upgrade_wanted_at"] = datetime.utcnow().isoformat()
            movie.meta = meta
            session.add(movie)
            session.commit()
            return {"status": "monitoring", "reason": reason}
        return {"status": "no_release", "reason": reason}

    old_files = _video_files(movie.folder_path)
    try:
        grab = orchestrator.grab(chosen, "movie", movie.folder_path,
                                 item_id=movie.id, source="upgrade")
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": str(exc)}

    meta = dict(movie.meta or {})
    meta["upgrade"] = {
        "grab_id": grab.id,
        "old_files": old_files,
        "release_title": grab.title,
        "from_score": current_movie_quality(movie),
        "to_score": quality_score(grab.title),
        "started_at": datetime.utcnow().isoformat(),
    }
    meta.pop("upgrade_wanted", None)
    meta.pop("upgrade_wanted_at", None)
    movie.meta = meta
    session.add(movie)
    session.commit()
    return {"status": "grabbed", "grab_id": grab.id, "release_title": grab.title}


def cancel_movie_upgrade(movie: Movie, session: Session) -> dict:
    """Clear the monitor flag and any pending-upgrade marker (does NOT touch an
    in-flight qBit download — drop that from the Activity queue if desired)."""
    meta = dict(movie.meta or {})
    had = bool(meta.pop("upgrade", None)) | bool(meta.pop("upgrade_wanted", None))
    meta.pop("upgrade_wanted_at", None)
    movie.meta = meta
    session.add(movie)
    session.commit()
    return {"status": "cancelled", "had_pending": bool(had)}


def finalize_movie_upgrade(grab: Grab, session: Session) -> bool:
    """Called by the importer after an upgrade grab imports. Removes the recorded old
    file(s), but ONLY once a genuinely new video file is present (so we never delete
    the only copy, e.g. if the chosen release had the same filename and was skipped)."""
    if grab.source != "upgrade" or grab.media_kind != "movie" or not grab.item_id:
        return False
    movie = session.get(Movie, grab.item_id)
    if not movie:
        return False
    info = (movie.meta or {}).get("upgrade") or {}
    if info.get("grab_id") != grab.id:
        return False

    # A torrent still downloading into this folder means any "new" video on disk
    # may be a preallocated partial (qBit creates files at full size at 0%) — not
    # proof the upgrade landed. Defer rather than delete the only good copy;
    # worst case both files linger, which is recoverable. (Belt to the importer's
    # _savepath_match_ok braces — a false import is how a 0% upgrade got here.)
    try:
        from ..downloaders import get_download_client
        folder = (movie.folder_path or "").rstrip("/")
        for t in get_download_client().torrents():
            sp = (t.get("save_path") or "").rstrip("/")
            if sp == folder and (t.get("progress") or 0) < 1.0:
                log.info("upgrade finalize deferred for movie %s — %r still downloading",
                         movie.id, t.get("name"))
                return False
    except Exception:
        log.warning("upgrade finalize: qbit check failed — proceeding", exc_info=True)

    old = {os.path.abspath(p) for p in (info.get("old_files") or [])}
    present = {os.path.abspath(p) for p in _video_files(movie.folder_path)}
    new_videos = present - old
    meta = dict(movie.meta or {})

    if not new_videos:
        # Nothing new actually landed — abort the swap rather than risk deleting the
        # last copy. Leave a breadcrumb so it's visible in Activity.
        log.warning("upgrade for movie %s imported no new video — keeping old files", movie.id)
        meta["upgrade"] = {**info, "result": "no_new_file"}
        movie.meta = meta
        session.add(movie)
        session.add(HistoryEvent(kind="failed", media_kind="movie", title=movie.title,
                                 message="upgrade aborted: imported release added no new video file",
                                 meta={"item_id": movie.id}))
        session.commit()
        notify(f"Upgrade aborted: {movie.title}",
               "The new release added no distinct video file — kept the existing copy.",
               kind="failed", media_kind="movie", priority="high")
        return False

    removed = []
    for p in old:
        if p in new_videos:        # paranoia: never delete a file we just imported
            continue
        try:
            if os.path.isfile(p):
                os.remove(p)
                removed.append(os.path.basename(p))
        except OSError as exc:
            log.warning("upgrade: could not remove old file %s: %s", p, exc)

    movie.file_size_bytes = sum(
        (os.path.getsize(p) for p in _video_files(movie.folder_path)
         if os.path.isfile(p)), 0) or None
    movie.has_file = True
    meta.pop("upgrade", None)
    movie.meta = meta
    session.add(movie)
    session.add(HistoryEvent(
        kind="upgraded", media_kind="movie", title=movie.title,
        message=f"replaced {len(removed)} old file(s) with {grab.title}",
        meta={"item_id": movie.id, "release": grab.title, "removed": removed,
              "from_score": info.get("from_score"), "to_score": info.get("to_score")}))
    session.commit()
    notify(f"Upgraded: {movie.title}", f"now {grab.title} (removed {len(removed)} old file(s))",
           kind="upgraded", media_kind="movie", meta={"item_id": movie.id})
    log.info("upgraded movie %s -> %s (removed %s)", movie.id, grab.title, removed)
    return True
