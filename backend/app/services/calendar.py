"""Release calendar.

``sync_calendar`` populates ``ReleaseEvent`` rows (idempotent upsert keyed by
media_kind + title + date + event_type) from:

- TV episode air-dates (already on ``Episode.air_date`` from the scanner /
  TVDB enrichment; we also pull missing dates from TVDB via providers).
- Movie release dates (TMDB release_date for monitored movies).
- Audiobook release dates (Audible catalog ``release_date`` for monitored
  series/authors).

``events`` lists a date range; ``process_auto_grab`` fires due auto_grab events.
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Optional

from sqlmodel import Session, select

from ..models import Audiobook, Episode, Movie, ReleaseEvent, Series
from ..pipeline import download_queue
from ..pipeline import orchestrator
from ..metadata import providers as P

log = logging.getLogger("managarr.calendar")


def _upsert_event(session: Session, *, media_kind: str, title: str, ev_date: str,
                  event_type: str = "release", item_id: Optional[int] = None,
                  meta: Optional[dict] = None) -> ReleaseEvent:
    if not ev_date:
        return None  # type: ignore[return-value]
    existing = session.exec(
        select(ReleaseEvent).where(
            ReleaseEvent.media_kind == media_kind,
            ReleaseEvent.title == title,
            ReleaseEvent.date == ev_date,
            ReleaseEvent.event_type == event_type,
        )
    ).first()
    if existing:
        if item_id and not existing.item_id:
            existing.item_id = item_id
        if meta:
            existing.meta = {**(existing.meta or {}), **meta}
        session.add(existing)
        return existing
    ev = ReleaseEvent(media_kind=media_kind, title=title, date=ev_date,
                      event_type=event_type, item_id=item_id, meta=meta)
    session.add(ev)
    return ev


def _sync_tv(session: Session) -> int:
    n = 0
    series_list = session.exec(select(Series).where(Series.monitored == True)).all()  # noqa: E712
    for series in series_list:
        eps = session.exec(select(Episode).where(Episode.series_id == series.id)).all()
        # If TVDB id present and we have no air dates, pull them.
        have_dates = any(e.air_date for e in eps)
        tvdb_id = (((series.meta or {}).get("providers") or {}).get("tvdb") or {}).get("id")
        if not have_dates and tvdb_id:
            try:
                for ep in P.tvdb_episodes(tvdb_id):
                    sn = ep.get("seasonNumber")
                    en = ep.get("number")
                    aired = ep.get("aired")
                    if sn is None or en is None or not aired:
                        continue
                    match = next((e for e in eps if e.season == sn and e.episode == en), None)
                    if match and not match.air_date:
                        match.air_date = aired
                        session.add(match)
            except Exception:
                log.exception("tvdb episode date pull failed for %s", series.title)
        for ep in eps:
            if not ep.air_date:
                continue
            title = f"{series.title} S{ep.season:02d}E{ep.episode:02d}"
            ev = _upsert_event(session, media_kind="tv", title=title, ev_date=ep.air_date,
                               event_type="air", item_id=series.id,
                               meta={"season": ep.season, "episode": ep.episode,
                                     "series_id": series.id})
            if ev:
                n += 1
    return n


def _sync_movies(session: Session) -> int:
    n = 0
    movies = session.exec(select(Movie).where(Movie.monitored == True)).all()  # noqa: E712
    for m in movies:
        tmdb_id = (((m.meta or {}).get("providers") or {}).get("tmdb") or {}).get("id")
        rd = None
        if tmdb_id:
            d = P.tmdb_movie(tmdb_id)
            rd = d.get("release_date") if d else None
        if not rd and m.year:
            continue  # no precise date; skip rather than invent
        if rd:
            ev = _upsert_event(session, media_kind="movie", title=m.title, ev_date=rd,
                               event_type="release", item_id=m.id)
            if ev:
                n += 1
    return n


def _sync_audiobooks(session: Session) -> int:
    n = 0
    books = session.exec(select(Audiobook).where(Audiobook.monitored == True)).all()  # noqa: E712
    authors = {b.author_name for b in books if b.author_name}
    for author in authors:
        try:
            products = P.audible_catalog(author=author, num=50)
        except Exception:
            log.exception("audible catalog failed for %s", author)
            continue
        for p in products:
            asin = p.get("asin")
            # publishedYear is a year string from the mapper; we need full date —
            # the mapper drops it, so reconstruct YYYY-01-01 when only a year is known.
            yr = p.get("publishedYear")
            rd = f"{yr}-01-01" if yr else None
            if not rd:
                continue
            ev = _upsert_event(session, media_kind="audiobook", title=p.get("title") or "",
                               ev_date=rd, event_type="release",
                               meta={"asin": asin, "author": author,
                                     "series": p.get("seriesName")})
            if ev:
                n += 1
    return n


def sync_calendar(session: Session) -> dict:
    tv = _sync_tv(session)
    movies = _sync_movies(session)
    audio = _sync_audiobooks(session)
    session.commit()
    return {"tv": tv, "movies": movies, "audiobooks": audio}


def events(session: Session, start: str, end: str) -> list[ReleaseEvent]:
    """List events whose ISO date falls within [start, end] inclusive."""
    stmt = select(ReleaseEvent).where(
        ReleaseEvent.date >= start, ReleaseEvent.date <= end
    ).order_by(ReleaseEvent.date)
    return session.exec(stmt).all()


def process_auto_grab(session: Session) -> dict:
    """For auto_grab events whose date<=today and not yet grabbed, fire auto_grab.

    Events whose library item is already owned (or no longer monitored) are marked
    grabbed WITHOUT enqueuing — auto_grab now defaults on for every event, so this
    guard is what keeps the backlog of past-dated events for an owned library from
    re-downloading everything.

    Auto-grab is release-driven: it only fires within ``CALENDAR_AUTO_GRAB_MAX_AGE_DAYS``
    of the event date (default 14). Older due events get their auto_grab expired
    instead of enqueued — the missing-item backfill (enqueue_missing / enqueue_tv's
    season-pack logic) owns old gaps, and firing per-episode grabs for a series'
    whole back-catalog would duplicate it as a download storm."""
    today = date.today().isoformat()
    max_age = int(os.getenv("CALENDAR_AUTO_GRAB_MAX_AGE_DAYS", "14"))
    cutoff = (date.today() - timedelta(days=max_age)).isoformat()
    due = session.exec(
        select(ReleaseEvent).where(
            ReleaseEvent.auto_grab == True,  # noqa: E712
            ReleaseEvent.grabbed == False,  # noqa: E712
            ReleaseEvent.date <= today,
        )
    ).all()
    grabbed = 0
    settled = 0
    expired = 0
    for ev in due:
        try:
            if ev.date < cutoff:
                ev.auto_grab = False
                session.add(ev)
                expired += 1
                continue
            if _already_satisfied(ev, session):
                ev.grabbed = True
                session.add(ev)
                settled += 1
                continue
            grab = _grab_for_event(ev, session)
            if grab:
                ev.grabbed = True
                session.add(ev)
                grabbed += 1
        except Exception:
            log.exception("auto_grab failed for event %s (%s)", ev.id, ev.title)
    session.commit()
    return {"due": len(due), "grabbed": grabbed, "already_owned": settled,
            "expired": expired}


def _already_satisfied(ev: ReleaseEvent, session: Session) -> bool:
    """True when there's nothing to grab for this event: the linked item already
    has its file, or it exists but is no longer monitored."""
    meta = ev.meta or {}
    if ev.media_kind == "movie" and ev.item_id:
        m = session.get(Movie, ev.item_id)
        return bool(m and (m.has_file or not m.monitored))
    if ev.media_kind == "tv" and ev.item_id:
        s = session.get(Series, ev.item_id)
        if s and not s.monitored:
            return True
        if meta.get("season") is not None and meta.get("episode") is not None:
            ep = session.exec(
                select(Episode).where(
                    Episode.series_id == ev.item_id,
                    Episode.season == meta["season"],
                    Episode.episode == meta["episode"],
                )
            ).first()
            return bool(ep and (ep.has_file or not ep.monitored))
        return False
    if ev.media_kind == "audiobook":
        title = (ev.title or "").lower().strip()
        if ev.item_id:
            b = session.get(Audiobook, ev.item_id)
            return bool(b and (b.has_audio or not b.monitored))
        b = next((x for x in session.exec(
            select(Audiobook).where(Audiobook.has_audio == True)).all()  # noqa: E712
            if x.title.lower().strip() == title), None)
        return b is not None
    return False


def _grab_for_event(ev: ReleaseEvent, session: Session):
    """ENQUEUE a due release event (source=calendar) for the throttled dispatcher,
    rather than firing at qBit immediately. Returns the queue row (truthy) when
    handled, so the caller can mark the event done."""
    meta = ev.meta or {}
    if ev.media_kind == "tv":
        series = session.get(Series, ev.item_id) if ev.item_id else None
        series_title = series.title if series else ev.title.rsplit(" S", 1)[0]
        season = meta.get("season")
        save_path = orchestrator.build_save_path("tv", title=series_title, season=season)
        return download_queue.enqueue_item(
            session, "tv", item_id=ev.item_id, title=ev.title, query=ev.title,
            save_path=save_path, source="calendar", series=series_title, season=season)
    if ev.media_kind == "movie":
        m = session.get(Movie, ev.item_id) if ev.item_id else None
        year = m.year if m else None
        save_path = orchestrator.build_save_path("movie", title=ev.title, year=year)
        query = f"{ev.title} {year}" if year else ev.title
        return download_queue.enqueue_item(
            session, "movie", item_id=ev.item_id, title=ev.title, query=query,
            save_path=save_path, source="calendar")
    if ev.media_kind == "audiobook":
        author = meta.get("author") or ""
        series = meta.get("series") or ""
        save_path = orchestrator.build_save_path("audiobook", title=ev.title,
                                                 author=author, series=series)
        return download_queue.enqueue_item(
            session, "audiobook", item_id=ev.item_id, title=ev.title, query=ev.title,
            save_path=save_path, source="calendar", author=author, series=series)
    return None
