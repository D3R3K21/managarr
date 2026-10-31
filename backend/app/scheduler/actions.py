"""Builtin scheduler actions.

Each action is a zero-arg callable that opens its OWN ``session_scope()`` (because
the scheduler runs them on background threads, not inside a request). ``BUILTIN_ACTIONS``
maps the action key stored on a ``ScheduledTask`` to its callable.
"""
from __future__ import annotations

import logging

from sqlmodel import select

from ..db import session_scope
from ..models import Audiobook, Movie, Series
from ..pipeline import download_queue as _dlq
from ..pipeline import orchestrator
from ..pipeline.importer import import_completed as _import_completed
from ..pipeline.scanner import scan_all as _scan_all
from ..metadata.aggregator import refresh_metadata
from ..services import calendar as calendar_svc
from ..services import collections as collections_svc
from ..services import transcode as transcode_svc
from ..services import watched_mover as watched_mover_svc
from ..services import watchlist as watchlist_svc

log = logging.getLogger("managarr.actions")


def scan_all() -> dict:
    with session_scope() as s:
        result = _scan_all(s)
    # The manual scan endpoints auto-enrich afterwards; the scheduled scan must
    # too, or rows the scan creates without artwork stay looking unmatched until
    # someone scans from the UI.
    try:
        from ..metadata.aggregator import enrich_missing
        with session_scope() as s:
            enrich_missing(s)
    except Exception:
        log.exception("post-scan enrich failed")
    return result


def import_completed() -> dict:
    with session_scope() as s:
        res = _import_completed(s)
    # A just-imported auto download frees a queue slot — backfill immediately
    # (fresh session so the dispatcher sees the committed Grab states).
    with session_scope() as s:
        res["queue"] = _dlq.process_queue(s)
    return res


def enqueue_auto_downloads() -> dict:
    """Top up the auto-download queue from monitored-missing items + the missing
    members of monitored ("watched") collections, then dispatch up to the
    concurrency cap. Replaces the old immediate-grab rss_search path."""
    with session_scope() as s:
        missing = _dlq.enqueue_missing(s)
    with session_scope() as s:
        tv = _dlq.enqueue_tv(s)
    with session_scope() as s:
        collections = _dlq.enqueue_collections(s)
    with session_scope() as s:
        dispatch = _dlq.process_queue(s)
    return {"missing": missing.get("enqueued", 0), "tv": tv.get("enqueued", 0),
            "collections": collections.get("enqueued", 0), "queue": dispatch}


def process_download_queue() -> dict:
    """Reconcile in-flight auto downloads and fill freed slots (no new enqueue)."""
    with session_scope() as s:
        return _dlq.process_queue(s)


def rss_search() -> dict:
    """Re-search monitored items that are missing files and auto_grab the best."""
    grabbed = 0
    checked = 0
    with session_scope() as s:
        movies = s.exec(select(Movie).where(Movie.monitored == True, Movie.has_file == False)).all()  # noqa: E712
        for m in list(movies):
            checked += 1
            save_path = orchestrator.build_save_path("movie", title=m.title, year=m.year)
            query = f"{m.title} {m.year}" if m.year else m.title
            try:
                if orchestrator.auto_grab("movie", query, save_path, item_id=m.id, source="rss"):
                    grabbed += 1
            except Exception:
                log.exception("rss_search movie %s failed", m.title)

        # monitored audiobooks that are genuinely absent (no audio at all). A book
        # present as MP3/FLAC is NOT missing — only lacking the preferred .m4b — so
        # we key off has_audio, never has_m4b, to avoid re-grabbing owned books.
        books = s.exec(select(Audiobook).where(Audiobook.monitored == True, Audiobook.has_audio == False)).all()  # noqa: E712
        for b in list(books):
            checked += 1
            save_path = orchestrator.build_save_path(
                "audiobook", title=b.title, author=b.author_name or "")
            try:
                if orchestrator.auto_grab("audiobook", b.title, save_path,
                                          author=b.author_name or "", item_id=b.id, source="rss"):
                    grabbed += 1
            except Exception:
                log.exception("rss_search audiobook %s failed", b.title)
    # Monitored quality upgrades ride the same cadence as the missing-item search.
    up = upgrade_search()
    return {"checked": checked, "grabbed": grabbed, "upgrades": up.get("upgraded", 0)}


def upgrade_search() -> dict:
    """Retry movies flagged 'monitor for upgrade' (meta.upgrade_wanted): re-search and,
    if a strictly-better profile-passing release now exists, kick off the upgrade.

    Gated by the automatic-downloads switch (it's a scheduled/background grab path).
    Movies with an upgrade already in flight (meta.upgrade) are skipped."""
    if not (orchestrator.downloads_enabled() and orchestrator.auto_downloads_enabled()):
        return {"checked": 0, "upgraded": 0, "skipped": "auto downloads disabled"}
    from ..pipeline import upgrade as upgrade_mod
    checked = upgraded = 0
    with session_scope() as s:
        wanted = [m.id for m in s.exec(select(Movie)).all()
                  if (m.meta or {}).get("upgrade_wanted") and not (m.meta or {}).get("upgrade")]
    for mid in wanted:
        checked += 1
        try:
            with session_scope() as s:
                m = s.get(Movie, mid)
                if m and upgrade_mod.start_movie_upgrade(m, s).get("status") == "grabbed":
                    upgraded += 1
        except Exception:
            log.exception("upgrade_search movie %s failed", mid)
    return {"checked": checked, "upgraded": upgraded}


def sync_calendar() -> dict:
    with session_scope() as s:
        return calendar_svc.sync_calendar(s)


def calendar_auto_grab() -> dict:
    with session_scope() as s:
        return calendar_svc.process_auto_grab(s)


def sync_watchlist() -> dict:
    with session_scope() as s:
        return watchlist_svc.sync_watchlist(s)


def move_watched_movies() -> dict:
    with session_scope() as s:
        return watched_mover_svc.move_watched_movies(s)


def transcode_scan() -> dict:
    with session_scope() as s:
        return transcode_svc.scan_candidates(s)


def transcode_process() -> dict:
    with session_scope() as s:
        return transcode_svc.process_queue(s)


def refresh_airing_series() -> dict:
    """Re-pull metadata + episode lists for monitored, not-ended series (empty
    status counts as continuing — it just means never enriched). Without this,
    episode stubs are only created once when a series is added, so new seasons'
    air dates never reach the calendar or the missing-episode enqueue."""
    with session_scope() as s:
        ids = [x.id for x in s.exec(
            select(Series).where(Series.monitored == True)).all()  # noqa: E712
            if (x.status or "") != "ended"]
    refreshed = 0
    for sid in ids:
        try:
            with session_scope() as s:
                refresh_metadata(sid, "tv", s)
            refreshed += 1
        except Exception:
            log.exception("refresh series %s failed", sid)
    return {"series": refreshed}


def refresh_metadata_all() -> dict:
    refreshed = {"movie": 0, "tv": 0, "audiobook": 0}
    with session_scope() as s:
        movie_ids = [m.id for m in s.exec(select(Movie)).all()]
        series_ids = [x.id for x in s.exec(select(Series)).all()]
        book_ids = [b.id for b in s.exec(select(Audiobook)).all()]
    for mid in movie_ids:
        try:
            with session_scope() as s:
                refresh_metadata(mid, "movie", s)
            refreshed["movie"] += 1
        except Exception:
            log.exception("refresh movie %s failed", mid)
    for sid in series_ids:
        try:
            with session_scope() as s:
                refresh_metadata(sid, "tv", s)
            refreshed["tv"] += 1
        except Exception:
            log.exception("refresh series %s failed", sid)
    for bid in book_ids:
        try:
            with session_scope() as s:
                refresh_metadata(bid, "audiobook", s)
            refreshed["audiobook"] += 1
        except Exception:
            log.exception("refresh audiobook %s failed", bid)
    return refreshed


def rebuild_collections() -> dict:
    with session_scope() as s:
        movies = collections_svc.rebuild_movie_collections(s)
    with session_scope() as s:
        series = collections_svc.rebuild_audiobook_series(s)
    return {"movies": movies, "audiobook_series": series}


def sync_audible() -> dict:
    """Sync Audible library from Libation: fetch new books, reconcile with DB."""
    from ..services import audible_sync
    with session_scope() as s:
        return audible_sync.sync(s)


def clean_abs_titles() -> dict:
    """Strip "(Unabridged)" and series-position tails from Audiobookshelf book titles."""
    from ..services import abs as abs_svc
    res = abs_svc.clean_titles()
    res.pop("titles", None)  # keep the task-run record small
    return res


BUILTIN_ACTIONS = {
    "scan_all": scan_all,
    "import_completed": import_completed,
    "rss_search": rss_search,
    "upgrade_search": upgrade_search,
    "enqueue_auto_downloads": enqueue_auto_downloads,
    "process_download_queue": process_download_queue,
    "sync_calendar": sync_calendar,
    "calendar_auto_grab": calendar_auto_grab,
    "sync_watchlist": sync_watchlist,
    "move_watched_movies": move_watched_movies,
    "transcode_scan": transcode_scan,
    "transcode_process": transcode_process,
    "refresh_airing_series": refresh_airing_series,
    "refresh_metadata_all": refresh_metadata_all,
    "rebuild_collections": rebuild_collections,
    "sync_audible": sync_audible,
    "clean_abs_titles": clean_abs_titles,
}
