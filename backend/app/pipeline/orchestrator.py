"""Acquisition orchestrator — the facade other modules call (CONTRACT).

``search`` / ``decide`` / ``grab`` / ``auto_grab`` are the only grab-path entry
points; routers, services and MCP call THESE rather than reimplementing logic.
"""
from __future__ import annotations

import logging
import os
import re

from .. import settings_store
from ..config import settings
from ..db import session_scope
from ..indexers.registry import get_indexers, search_all
from ..models import Blocklist, Grab, HistoryEvent
from ..notify import notify
from .decision import pick_best, quality_profile_for
from .qbit import QBitClient, infohash_from_torrent

log = logging.getLogger("managarr.orchestrator")

_SANITIZE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize(segment: str) -> str:
    """Make one path segment filesystem-safe (no separators / illegal chars)."""
    s = _SANITIZE_RE.sub("", segment or "").strip().rstrip(". ")
    return s or "untitled"


def _truthy(*vals) -> bool:
    """First non-None value parsed as a bool (runtime override, then config default)."""
    for v in vals:
        if v is None:
            continue
        return str(v).strip().lower() in ("1", "true", "yes", "on")
    return False


def _pref_bool(key: str, default) -> bool:
    """A boolean preference: runtime override (settings table) wins, else config."""
    return _truthy(settings_store.get_setting(key, None), default)


# Sources that originate from a scheduled/background job (not a user button click).
# These are gated by AUTO_DOWNLOAD_ENABLED; manual sources ("manual"/"missing") are not.
AUTOMATIC_SOURCES = {"rss", "calendar", "watchlist"}


def downloads_enabled() -> bool:
    """Master switch — is the qBittorrent integration allowed to send anything?"""
    return _pref_bool("DOWNLOADS_ENABLED", settings.DOWNLOADS_ENABLED)


def auto_downloads_enabled() -> bool:
    """Are the automatic (scheduled) grab paths allowed to download?"""
    return _pref_bool("AUTO_DOWNLOAD_ENABLED", settings.AUTO_DOWNLOAD_ENABLED)


# ── path building (CONTRACT save-path convention) ─────────────────────────────
def build_save_path(media_kind: str, *, title: str, year: int | None = None,
                    season: int | None = None, author: str = "", series: str = "") -> str:
    if media_kind == "movie":
        name = f"{sanitize(title)} ({year})" if year else sanitize(title)
        # Movies download into the New Movies staging library; the watched-mover
        # relocates them to MOVIES_PATH once watched in Plex.
        return os.path.join(settings.NEW_MOVIES_PATH, name)
    if media_kind in ("tv", "series"):
        season_dir = f"Season {int(season):02d}" if season is not None else "Season 01"
        return os.path.join(settings.TV_PATH, sanitize(title), season_dir)
    if media_kind == "audiobook":
        parts = [settings.AUDIOBOOKS_PATH]
        if author:
            parts.append(sanitize(author))
        if series:
            parts.append(sanitize(series))
        parts.append(sanitize(title))
        return os.path.join(*parts)
    return os.path.join(settings.DOWNLOADS_PATH, sanitize(title))


# ── facade ────────────────────────────────────────────────────────────────────
def search(media_kind: str, query: str, author: str = "") -> list[dict]:
    with session_scope() as s:
        return search_all(media_kind, query, s, author=author)


def release_key(title: str | None) -> str:
    """Normalize a release title for blocklist comparison — release names vary only
    in separators ('.', ' ', '-') between indexer listing and stored block row."""
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())


def candidates(releases: list[dict], media_kind: str, profile_id: int | None = None) -> tuple[list[dict], object]:
    """Profile-passing, non-blocklisted releases + the profile used. Shared by decide()
    (seeder-ranked grab) and the quality-ranked upgrade path."""
    from sqlmodel import select
    from .decision import acceptable_releases

    with session_scope() as s:
        if profile_id is not None:
            from ..models import QualityProfile
            profile = s.get(QualityProfile, profile_id)
        else:
            profile = quality_profile_for(media_kind, s)
        rows = s.exec(select(Blocklist)).all()
        blocked = {(b.infohash or "").lower() for b in rows if b.infohash}
        # Title match is the fallback for indexers whose search results carry no
        # infohash (IPTorrents) — an infohash-only block never fires for those.
        blocked_titles = {release_key(b.title) for b in rows if b.title}
        unblocked = [r for r in releases
                     if (r.get("infohash") or "").lower() not in blocked
                     and release_key(r.get("title")) not in blocked_titles]
        cands = acceptable_releases(unblocked, profile)
        # Detach the profile with its attributes already loaded (acceptable_releases
        # above read them) so callers — decide()/the upgrade path call pick_best AFTER
        # this session closes — can keep reading profile.* without session_scope's
        # exit-commit expiring them -> DetachedInstanceError. Same pattern as grab().
        if profile is not None:
            s.expunge(profile)
        return cands, profile


def decide(releases: list[dict], media_kind: str, profile_id: int | None = None,
           prefer_pack: bool = False, want_title: str | None = None) -> dict | None:
    cands, profile = candidates(releases, media_kind, profile_id=profile_id)
    # Fuzzy-match guard: the indexer's search is a loose full-text match, so drop
    # releases whose title isn't actually the requested show/movie (searching
    # "House" must not settle for "The Fall of the House of Usher").
    if want_title:
        from .decision import title_relevant
        kept = [r for r in cands if title_relevant(r.get("title", ""), want_title)]
        if len(kept) != len(cands):
            log.info("decide: title-relevance dropped %d/%d release(s) for %r",
                     len(cands) - len(kept), len(cands), want_title)
        cands = kept
    return pick_best(cands, profile, prefer_pack=prefer_pack)


def _find_indexer(media_kind: str, indexer_name: str):
    with session_scope() as s:
        for ix in get_indexers(media_kind, s):
            if ix.name == indexer_name:
                return ix
    return None


def grab(release: dict, media_kind: str, save_path: str, *, item_id=None, source="manual") -> Grab:
    """Fetch the .torrent via its indexer, add to qBit, record a Grab + history."""
    if not downloads_enabled():
        raise RuntimeError("downloads are disabled (qBittorrent integration is turned off in Settings)")
    indexer_name = release.get("indexer", "")
    ix = _find_indexer(media_kind, indexer_name)
    if not ix:
        raise RuntimeError(f"no enabled indexer named {indexer_name!r} for {media_kind}")

    os.makedirs(save_path, exist_ok=True)
    torrent_bytes = ix.fetch_torrent(release)

    filename = sanitize(release.get("title", "download")) + ".torrent"
    ratio = settings.QBT_SEED_RATIO or None
    qbit = QBitClient()
    ok = qbit.add_torrent(torrent_bytes, save_path, settings.QBT_CATEGORY, filename, ratio=ratio)
    if not ok:
        raise RuntimeError("qBittorrent rejected the torrent")

    # Apply the operator's download-priority preferences so grabs don't sink to the
    # bottom of the queue behind stuck torrents. Resolve the new torrent's infohash
    # once (indexer-provided, else computed from the .torrent) and apply both the
    # queue-position preference and the (orthogonal) force-start preference.
    mode = (settings_store.get_setting("QBT_QUEUE_PRIORITY", None)
            or settings.QBT_QUEUE_PRIORITY or "none").strip().lower()
    force = _truthy(settings_store.get_setting("QBT_FORCE_START", None), settings.QBT_FORCE_START)
    if mode not in ("", "none", "normal") or force:
        infohash = (release.get("infohash") or "").lower() or (infohash_from_torrent(torrent_bytes) or "")
        if infohash:
            try:
                if mode not in ("", "none", "normal"):
                    qbit.apply_queue_priority(infohash, mode)
                if force:
                    qbit.set_force_start(infohash, True)
            except Exception:
                log.warning("could not apply download priority (mode=%s force=%s) to %s",
                            mode, force, infohash, exc_info=True)
        else:
            log.info("download priority requested but no infohash available for %r", filename)

    with session_scope() as s:
        g = Grab(
            media_kind=media_kind,
            item_id=item_id,
            title=release.get("title", ""),
            indexer=indexer_name,
            download_url=release.get("download_url"),
            infohash=release.get("infohash"),
            save_path=save_path,
            category=settings.QBT_CATEGORY,
            size_bytes=release.get("size_bytes"),
            seeders=release.get("seeders"),
            source=source,
            state="grabbed",
        )
        s.add(g)
        s.add(HistoryEvent(kind="grabbed", media_kind=media_kind, title=g.title,
                           message=f"from {indexer_name} -> {save_path}",
                           meta={"source": source, "seeders": g.seeders, "item_id": item_id}))
        s.commit()
        s.refresh(g)
        # Detach with attributes loaded so callers (the queue dispatcher, the manual
        # grab endpoint) can read g.id/g.infohash after the session closes — without
        # this, session_scope's exit-commit expires them -> DetachedInstanceError.
        s.expunge(g)

    notify(f"Grabbed: {release.get('title','')}", f"{indexer_name} -> {save_path}",
           kind="grabbed", media_kind=media_kind)
    return g


def auto_grab(media_kind: str, query: str, save_path: str, *, author="", item_id=None,
              source="rss", want_title: str | None = None) -> Grab | None:
    """search_all -> pick_best (skip blocklisted) -> grab the winner, or None.

    ``want_title`` is the clean title used to reject fuzzy indexer matches; it
    defaults to ``query`` (whose trailing year/season is stripped by the guard)."""
    if not downloads_enabled():
        log.info("auto_grab skipped (downloads disabled): %r", query)
        return None
    if source in AUTOMATIC_SOURCES and not auto_downloads_enabled():
        log.info("auto_grab skipped (automatic downloads disabled): %r [%s]", query, source)
        return None
    # User-initiated adds (search/manual) should not fail silently — surface a
    # history event/notification so it's visible in Activity. Automatic bulk sources
    # (rss/calendar/watchlist) stay quiet to avoid spamming the log.
    user_initiated = source not in AUTOMATIC_SOURCES
    releases = search(media_kind, query, author=author)
    if not releases:
        log.info("auto_grab: no releases found for %r", query)
        if user_initiated:
            notify(f"No release found: {query}",
                   "The indexer returned no results (it may be rate-limited/unavailable). "
                   "Try again, or open the title to search releases manually.",
                   kind="failed", media_kind=media_kind)
        return None
    best = decide(releases, media_kind, want_title=want_title or query)
    if not best:
        log.info("auto_grab: no release passed the quality profile for %r", query)
        if user_initiated:
            notify(f"No release passed the quality profile: {query}",
                   "Found results but none matched your quality profile — adjust the profile "
                   "or pick a release manually from the title page.",
                   kind="failed", media_kind=media_kind)
        return None
    return grab(best, media_kind, save_path, item_id=item_id, source=source)
