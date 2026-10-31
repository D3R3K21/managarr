"""Throttled auto-download queue — the dispatcher for automatic grabs.

Automatic sources (monitored-missing items, and later watchlist/calendar/watched
collections) ENQUEUE a ``DownloadQueueItem`` instead of grabbing immediately. A
dispatcher then sends at most ``AUTO_DOWNLOAD_MAX_CONCURRENT`` of them to qBit at
once; as each finishes (its linked Grab imports) a slot frees and the next queued
item is sent. Manual grabs bypass this entirely (they call ``orchestrator.grab``
directly and are never queued), so manual always takes priority over auto.

Mirrors the transcode service's queue/cap pattern. Completion detection is free:
the importer already transitions ``Grab.state`` grabbed->importing->imported by
infohash, so ``reconcile_active`` just reads the linked Grab.

CONTRACT: this module owns the auto-grab decision; callers ENQUEUE, the scheduler
ticks ``process_queue``. Phase 1 enqueues only the monitored-missing path.
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta

from sqlmodel import Session, select

from .. import settings_store
from ..config import settings
from ..models import (Audiobook, Collection, CollectionMember,
                      DownloadQueueItem, Episode, Grab, HistoryEvent, Movie, Series)
from . import orchestrator

log = logging.getLogger("managarr.download_queue")

# Queue rows that should block re-enqueue of the same item (work is "open").
_OPEN_STATES = ["queued", "active", "no_release"]
# Library models by media_kind (for releaseless flagging + future tv).
_MODELS = {"movie": Movie, "tv": Series, "audiobook": Audiobook}
# Per-source dispatch priority (lower = sooner). CONTRACT: watchlist > collection
# /calendar > rss/missing (a thing you explicitly watchlisted jumps the bulk
# backfill).
SOURCE_PRIORITY = {
    "watchlist": 10,
    "watched_collection": 20,
    "collection": 20,
    "calendar": 20,
    "missing": 30,
    "rss": 30,
}


def _is_released(release_date) -> bool:
    """True only if ``release_date`` (ISO 'YYYY-MM-DD') is set and not in the future.
    ISO dates compare lexicographically, so a string compare to today suffices."""
    if not release_date:
        return False
    from datetime import date
    return str(release_date)[:10] <= date.today().isoformat()


def _int_setting(key: str, default: int) -> int:
    """Runtime override (Settings store) wins, else the config default."""
    raw = settings_store.get_setting(key, None)
    try:
        return int(raw) if raw not in (None, "") else int(default)
    except (TypeError, ValueError):
        return int(default)


# ── enqueue (CONTRACT entry point for every automatic source) ─────────────────
def enqueue_item(session: Session, media_kind: str, *, item_id, title: str,
                 query: str, save_path: str, source: str,
                 author: str | None = None, series: str | None = None,
                 season: int | None = None) -> DownloadQueueItem | None:
    """Add ONE automatic download intent (the only way sources enter the queue).

    Idempotent: if an open row already exists for this (media_kind, item_id) it is
    NOT duplicated — but its priority is UPGRADED if this source ranks higher (so a
    movie first queued by the rss backfill becomes priority-10 when the watchlist
    sync also wants it, regardless of which ran first). Skips items already flagged
    ``releaseless`` (parked) — those wait for an explicit retry. Returns the row
    (existing or new), or None if skipped."""
    priority = SOURCE_PRIORITY.get(source, 30)
    model = _MODELS.get(media_kind)
    if model and item_id is not None:
        obj = session.get(model, item_id)
        if obj and (obj.meta or {}).get("acq_status") == "releaseless":
            return None

    # Dedup against an open row by (item_id, query). Query is part of the key so a
    # single TV series can hold several open rows — one per season pack / episode —
    # while a movie/book (stable query) still de-dupes to one.
    existing = session.exec(
        select(DownloadQueueItem).where(
            DownloadQueueItem.media_kind == media_kind,
            DownloadQueueItem.state.in_(_OPEN_STATES),
            DownloadQueueItem.item_id == item_id,
            DownloadQueueItem.query == query,
        )
    ).first()
    if existing:
        if priority < existing.priority:
            existing.priority = priority
            existing.source = source
            session.add(existing)
        return existing

    row = DownloadQueueItem(
        media_kind=media_kind, item_id=item_id, title=title, query=query,
        save_path=save_path, source=source, priority=priority,
        author=author, series=series, season=season)
    session.add(row)
    session.flush()
    return row


def enqueue_missing(session: Session) -> dict:
    """Top up the queue from monitored items that are missing their file (the rss
    backfill). Skips items with an open queue row or flagged ``releaseless``."""
    if not orchestrator.auto_downloads_enabled():
        return {"enqueued": 0, "status": "automatic downloads disabled"}

    # pre-existing open keys, so the count reflects only NEW enqueues this run
    pre = {(r.media_kind, r.item_id) for r in session.exec(
        select(DownloadQueueItem).where(DownloadQueueItem.state.in_(_OPEN_STATES))).all()}

    enq = 0
    movies = session.exec(
        select(Movie).where(Movie.monitored == True, Movie.has_file == False)  # noqa: E712
    ).all()
    for m in movies:
        query = f"{m.title} {m.year}" if m.year else m.title
        save_path = orchestrator.build_save_path("movie", title=m.title, year=m.year)
        if enqueue_item(session, "movie", item_id=m.id, title=m.title, query=query,
                        save_path=save_path, source="missing") and ("movie", m.id) not in pre:
            enq += 1

    # monitored audiobooks with NO audio present (keyed off has_audio, never
    # has_m4b — matches rss_search, so an owned MP3-only book isn't re-grabbed).
    books = session.exec(
        select(Audiobook).where(Audiobook.monitored == True, Audiobook.has_audio == False)  # noqa: E712
    ).all()
    for b in books:
        save_path = orchestrator.build_save_path(
            "audiobook", title=b.title, author=b.author_name or "")
        if enqueue_item(session, "audiobook", item_id=b.id, title=b.title, query=b.title,
                        save_path=save_path, source="missing", author=b.author_name or "") \
                and ("audiobook", b.id) not in pre:
            enq += 1

    if enq:
        log.info("enqueue_missing: queued %d item(s)", enq)
    return {"enqueued": enq}


def enqueue_collections(session: Session) -> dict:
    """Enqueue the MISSING members of every monitored ("watched") collection so the
    dispatcher fills the gaps (source=watched_collection, priority above rss).

    Members with no owned library row yet are enqueued by search query (item_id
    None) and de-duped on that query; the scanner + relink attach the file to the
    collection once it imports."""
    if not orchestrator.auto_downloads_enabled():
        return {"enqueued": 0, "status": "automatic downloads disabled"}

    pre_ids = {r.id for r in session.exec(
        select(DownloadQueueItem).where(DownloadQueueItem.state.in_(_OPEN_STATES))).all()}

    enq = 0
    cols = session.exec(select(Collection).where(Collection.monitored == True)).all()  # noqa: E712
    for col in cols:
        members = session.exec(
            select(CollectionMember).where(
                CollectionMember.collection_id == col.id,
                CollectionMember.owned == False,  # noqa: E712
            )
        ).all()
        for mem in members:
            # Skip not-yet-released members (no release date, or a future one) — a
            # collection's TMDB parts include announced/unreleased sequels (e.g.
            # "Avatar 5") that have no torrent and would churn in "searching" forever.
            if not _is_released(mem.release_date):
                continue
            mk = mem.item_kind or ("audiobook" if col.kind == "audiobook" else "movie")
            if mk == "movie":
                query = f"{mem.title} {mem.year}" if mem.year else mem.title
                save_path = orchestrator.build_save_path("movie", title=mem.title, year=mem.year)
                row = enqueue_item(session, "movie", item_id=mem.item_id, title=mem.title,
                                   query=query, save_path=save_path, source="watched_collection")
            elif mk == "audiobook":
                author = (mem.meta or {}).get("author") or ""
                save_path = orchestrator.build_save_path(
                    "audiobook", title=mem.title, author=author, series=col.name)
                row = enqueue_item(session, "audiobook", item_id=mem.item_id, title=mem.title,
                                   query=mem.title, save_path=save_path,
                                   source="watched_collection", author=author, series=col.name)
            else:
                continue
            if row and row.id not in pre_ids:
                enq += 1

    if enq:
        log.info("enqueue_collections: queued %d member(s)", enq)
    return {"enqueued": enq}


# ── TV: missing-episode acquisition (season pack preferred) ───────────────────
def _enqueue_series(session: Session, sr: Series, pre_ids: set) -> int:
    """Enqueue acquisition intents for one series' AIRED, monitored, missing
    episodes: a season pack when most of a season is missing, else per-episode for
    one-off gaps. Returns the number of NEW queue rows created."""
    from datetime import date
    today = date.today().isoformat()
    eps = session.exec(
        select(Episode).where(Episode.series_id == sr.id, Episode.monitored == True)  # noqa: E712
    ).all()
    by_season: dict = {}
    for e in eps:
        if e.season is not None and e.season >= 1:
            by_season.setdefault(e.season, []).append(e)

    enq = 0
    for sn, seps in by_season.items():
        aired = [e for e in seps if e.air_date and e.air_date <= today]
        if not aired:
            continue
        missing = [e for e in aired if not e.has_file]
        if not missing:
            continue
        have = [e for e in aired if e.has_file]
        save = orchestrator.build_save_path("tv", title=sr.title, season=sn)
        # skip a season that already has a grab in flight (e.g. a manual season grab)
        inflight = session.exec(
            select(Grab).where(Grab.media_kind == "tv", Grab.save_path == save,
                               Grab.state.in_(["grabbed", "downloading", "importing"]))
        ).first()
        if inflight:
            continue
        # whole/most of the season missing -> one season pack; a few gaps -> per-episode
        if not have or len(missing) >= max(2, len(aired) // 2):
            query = f"{sr.title} S{sn:02d}"
            row = enqueue_item(session, "tv", item_id=sr.id, title=f"{sr.title} Season {sn}",
                               query=query, save_path=save, source="missing",
                               series=sr.title, season=sn)
            if row and row.id not in pre_ids:
                enq += 1
        else:
            for e in missing:
                query = f"{sr.title} S{sn:02d}E{e.episode:02d}"
                row = enqueue_item(session, "tv", item_id=sr.id,
                                   title=f"{sr.title} S{sn:02d}E{e.episode:02d}",
                                   query=query, save_path=save, source="missing",
                                   series=sr.title, season=sn)
                if row and row.id not in pre_ids:
                    enq += 1
    return enq


def enqueue_tv(session: Session) -> dict:
    """Enqueue missing episodes (season-pack preferred) for every monitored series."""
    if not orchestrator.auto_downloads_enabled():
        return {"enqueued": 0, "status": "automatic downloads disabled"}
    pre_ids = {r.id for r in session.exec(
        select(DownloadQueueItem).where(DownloadQueueItem.state.in_(_OPEN_STATES))).all()}
    enq = 0
    for sr in session.exec(select(Series).where(Series.monitored == True)).all():  # noqa: E712
        enq += _enqueue_series(session, sr, pre_ids)
    if enq:
        log.info("enqueue_tv: queued %d season/episode intent(s)", enq)
    return {"enqueued": enq}


def enqueue_tv_for_series(session: Session, series_id: int) -> dict:
    """Enqueue one series' missing episodes (used by the search 'add + download')."""
    sr = session.get(Series, series_id)
    if not sr:
        return {"enqueued": 0, "status": "series not found"}
    pre_ids = {r.id for r in session.exec(
        select(DownloadQueueItem).where(DownloadQueueItem.state.in_(_OPEN_STATES))).all()}
    return {"enqueued": _enqueue_series(session, sr, pre_ids)}


# ── dispatch ──────────────────────────────────────────────────────────────────
def _flag_releaseless(item: DownloadQueueItem, session: Session) -> None:
    """Mark the library item ``releaseless`` so it drops out of the normal library
    view (a Releaseless filter surfaces it for manual delete / re-search). Never
    deletes anything."""
    model = _MODELS.get(item.media_kind)
    if model and item.item_id:
        obj = session.get(model, item.item_id)
        if obj:
            meta = dict(obj.meta or {})
            meta["acq_status"] = "releaseless"
            meta["releaseless_at"] = datetime.utcnow().isoformat()
            obj.meta = meta            # reassign (not mutate) so JSON change is detected
            session.add(obj)
    session.add(HistoryEvent(
        kind="parked", media_kind=item.media_kind, title=item.title,
        message=f"no acceptable release after {item.attempts} attempt(s) — flagged releaseless"))


def _backoff_or_park(item: DownloadQueueItem, session: Session) -> None:
    """A search attempt failed to yield a grab: back off, or park if exhausted."""
    max_attempts = _int_setting("AUTO_DOWNLOAD_MAX_ATTEMPTS", settings.AUTO_DOWNLOAD_MAX_ATTEMPTS)
    if item.attempts >= max_attempts:
        item.state = "parked"
        item.finished_at = datetime.utcnow()
        _flag_releaseless(item, session)
    else:
        item.state = "no_release"
        interval = _int_setting("AUTO_DOWNLOAD_SYNC_INTERVAL_MINUTES",
                                settings.AUTO_DOWNLOAD_SYNC_INTERVAL_MINUTES)
        # linear backoff: attempt N waits N * interval before re-searching
        item.next_retry_at = datetime.utcnow() + timedelta(minutes=interval * item.attempts)


def _already_have_file(item: DownloadQueueItem, session: Session) -> bool:
    """True if the linked single-item already owns its file. Ownership is checked at
    enqueue time (watchlist `_already_owned`, monitored `has_file == False`), but a
    queued row can go stale: you manually grab + import the same title after it was
    queued. Re-checking here keeps the dispatcher from grabbing a duplicate. (Movies
    use has_file, audiobooks has_audio; tv items are per-season packs with no single
    file flag, so they're left to per-episode enqueue de-dup.)"""
    if not item.item_id:
        return False
    if item.media_kind == "movie":
        m = session.get(Movie, item.item_id)
        return bool(m and m.has_file)
    if item.media_kind == "audiobook":
        b = session.get(Audiobook, item.item_id)
        return bool(b and getattr(b, "has_audio", False))
    return False


def _grab_in_flight(item: DownloadQueueItem, session: Session) -> Grab | None:
    """A grab for the same item that hasn't finished importing yet (e.g. a manual
    search grabbed it after this row was queued, and it's still downloading)."""
    if not item.item_id:
        return None
    return session.exec(
        select(Grab).where(Grab.media_kind == item.media_kind, Grab.item_id == item.item_id,
                           Grab.state.in_(["grabbed", "downloading", "importing"]))
    ).first()


def _try_dispatch(item: DownloadQueueItem, session: Session) -> bool:
    """Search -> decide -> grab one queue item. Returns True if it went active."""
    # Already have it (manually grabbed after this row was queued)? Don't dupe.
    if _already_have_file(item, session):
        item.state = "done"
        item.finished_at = datetime.utcnow()
        item.last_error = None
        session.add(item)
        log.info("dispatch skipped %r: already owned (has file)", item.title)
        return False
    # Already downloading under another grab? Don't fetch a second copy.
    if item.media_kind != "tv" and (busy := _grab_in_flight(item, session)):
        item.state = "done"
        item.finished_at = datetime.utcnow()
        item.last_error = None
        session.add(item)
        log.info("dispatch skipped %r: already downloading (grab %s, %s)", item.title, busy.id, busy.source)
        return False
    # TV season intent (query has no SxxExx) -> prefer a season/complete pack.
    prefer_pack = item.media_kind == "tv" and not re.search(r"s\d{1,2}\s*e\d", (item.query or "").lower())
    try:
        releases = orchestrator.search(item.media_kind, item.query, author=item.author or "")
        # Guard against fuzzy indexer matches: judge relevance against the clean
        # show/movie name (item.series for TV, else the display title), not the
        # search query which carries SxxExx/year noise.
        want_title = item.series or item.title
        best = orchestrator.decide(releases, item.media_kind, prefer_pack=prefer_pack,
                                   want_title=want_title)
    except Exception as exc:  # noqa: BLE001
        item.attempts += 1
        item.last_error = f"search failed: {exc}"[:300]
        _backoff_or_park(item, session)
        session.add(item)
        log.warning("dispatch search failed for %r: %s", item.title, exc)
        return False

    if not best:
        item.attempts += 1
        item.last_error = "no acceptable release"
        _backoff_or_park(item, session)
        session.add(item)
        return False

    try:
        grab = orchestrator.grab(best, item.media_kind, item.save_path,
                                 item_id=item.item_id, source=item.source)
    except Exception as exc:  # noqa: BLE001
        item.attempts += 1
        item.last_error = f"grab failed: {exc}"[:300]
        _backoff_or_park(item, session)
        session.add(item)
        log.warning("dispatch grab failed for %r: %s", item.title, exc)
        return False

    item.grab_id = grab.id
    item.infohash = grab.infohash
    item.state = "active"
    item.dispatched_at = datetime.utcnow()
    item.last_error = None
    session.add(item)
    return True


def delete_for_item(session: Session, media_kind: str, item_id, delete_files: bool = False) -> int:
    """Remove every queue row tied to a (just-deleted) library item, and stop any
    in-flight qBit torrent for an active one. ``delete_files`` also removes the
    torrent's downloaded data (e.g. partial files). Returns the number of rows
    removed."""
    if item_id is None:
        return 0
    rows = session.exec(
        select(DownloadQueueItem).where(
            DownloadQueueItem.media_kind == media_kind,
            DownloadQueueItem.item_id == item_id,
        )
    ).all()
    qbit = None
    for r in rows:
        if r.state == "active" and r.infohash:
            try:
                if qbit is None:
                    from .qbit import QBitClient
                    qbit = QBitClient()
                qbit.delete_torrent(r.infohash, delete_files=delete_files)
            except Exception:  # noqa: BLE001
                log.warning("could not remove qBit torrent for queue item %s", r.id)
        session.delete(r)
    if rows:
        log.info("deleted %d queue row(s) for %s item %s", len(rows), media_kind, item_id)
    return len(rows)


def reconcile_active(session: Session) -> int:
    """Resolve ``active`` items by reading their linked Grab. Returns the number
    still active afterward (the live concurrency count)."""
    active = session.exec(
        select(DownloadQueueItem).where(DownloadQueueItem.state == "active")
    ).all()
    still = 0
    for item in active:
        g = session.get(Grab, item.grab_id) if item.grab_id else None
        if g is None:
            item.state = "failed"
            item.last_error = "grab row missing"
            item.finished_at = datetime.utcnow()
            session.add(item)
        elif g.state == "imported":
            item.state = "done"
            item.finished_at = datetime.utcnow()
            session.add(item)
        elif g.state == "failed":
            item.state = "failed"
            item.last_error = g.message or "grab failed"
            item.finished_at = datetime.utcnow()
            session.add(item)
        else:
            still += 1
    return still


# Several scheduled jobs (the dispatcher tick, watchlist sync, the enqueue scan, search)
# end by calling process_queue on their own scheduler threads. Unserialized, two of
# them picked the same queued row in the same instant and grabbed it twice.
_dispatch_lock = threading.Lock()


def process_queue(session: Session) -> dict:
    """Reconcile active items, then fill free slots up to the concurrency cap.
    One dispatch at a time; a caller that finds one running skips (the 2-minute tick
    picks up anything it left)."""
    if not _dispatch_lock.acquire(blocking=False):
        return {"status": "busy", "dispatched": 0}
    try:
        result = _process_queue(session)
        # Commit before letting the next dispatcher in, so it sees these rows as taken.
        session.commit()
        return result
    finally:
        _dispatch_lock.release()


def _process_queue(session: Session) -> dict:
    if not orchestrator.downloads_enabled():
        return {"status": "downloads disabled", "dispatched": 0}
    if not orchestrator.auto_downloads_enabled():
        return {"status": "automatic downloads disabled", "dispatched": 0}

    active_count = reconcile_active(session)
    cap = _int_setting("AUTO_DOWNLOAD_MAX_CONCURRENT", settings.AUTO_DOWNLOAD_MAX_CONCURRENT)
    slots = max(0, cap - active_count)
    if slots <= 0:
        return {"status": "at capacity", "active": active_count, "cap": cap, "dispatched": 0}

    now = datetime.utcnow()
    candidates = session.exec(
        select(DownloadQueueItem)
        .where(DownloadQueueItem.state.in_(["queued", "no_release"]))
        .order_by(DownloadQueueItem.priority, DownloadQueueItem.created_at)
    ).all()
    # no_release items wait for their backoff window
    eligible = [c for c in candidates
                if c.state == "queued" or c.next_retry_at is None or c.next_retry_at <= now]

    dispatched = 0
    for item in eligible[:slots]:
        if _try_dispatch(item, session):
            dispatched += 1
    return {"status": "ok", "active": active_count, "cap": cap,
            "dispatched": dispatched, "queued": len(candidates)}
