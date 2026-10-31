"""Metadata aggregation / refresh.

``refresh_metadata(item_id, kind, session)`` fills missing canonical fields and
``item.meta["providers"]`` from the providers (priority order), pulls trailers
(movies/tv) and a synopsis (books), then writes the on-disk sidecar.

Priority:
- movie: TMDB (canonical) -> imdb id from TMDB external_ids
- tv:    TVDB (canonical) -> TMDB external_ids cross-link if available
- audiobook: Audnexus (canonical) -> Google Books / OpenLibrary fallback synopsis
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from sqlmodel import Session, select

from ..config import settings
from ..models import Audiobook, Episode, Movie, Series
from . import providers as P
from .sidecar import build_metadata_dict, write_sidecar, _strip_html

log = logging.getLogger("managarr.aggregator")

# Live progress for the bulk metadata-enrich pass, surfaced via GET /api/scan/status.
ENRICH_PROGRESS: dict[str, dict] = {}

_IMG = P.TMDB_IMAGE_BASE


def _meta(item) -> dict:
    m = dict(item.meta or {})
    m.setdefault("providers", {})
    return m


# ── movies ────────────────────────────────────────────────────────────────────
def enrich_movie(movie: Movie, session: Session) -> None:
    meta = _meta(movie)
    providers = meta["providers"]
    tmdb_id = (providers.get("tmdb") or {}).get("id")

    if not tmdb_id:
        # try to resolve by title (+year)
        q = f"{movie.title}"
        results = P.tmdb_search_movie(q)
        if movie.year:
            results = [r for r in results if str(r.get("release_date", ""))[:4] == str(movie.year)] or results
        if results:
            tmdb_id = results[0].get("id")

    if not tmdb_id:
        log.info("no tmdb match for movie %s", movie.title)
        return

    d = P.tmdb_movie(tmdb_id)
    if not d:
        return

    movie.title = d.get("title") or movie.title
    if d.get("release_date"):
        movie.year = int(d["release_date"][:4]) if d["release_date"][:4].isdigit() else movie.year
    movie.overview = d.get("overview") or movie.overview
    if d.get("poster_path"):
        movie.poster_url = _IMG + d["poster_path"]
    if d.get("runtime"):
        movie.runtime_seconds = int(d["runtime"]) * 60

    providers["tmdb"] = {"id": tmdb_id, "url": f"https://www.themoviedb.org/movie/{tmdb_id}"}
    imdb = (d.get("external_ids") or {}).get("imdb_id") or d.get("imdb_id")
    if imdb:
        providers["imdb"] = {"id": imdb}
    meta["genres"] = [g.get("name") for g in (d.get("genres") or []) if g.get("name")]
    cast = (d.get("credits") or {}).get("cast") or []
    meta["people"] = [
        {"role": "actor", "name": c.get("name"), "character": c.get("character", ""),
         "providers": {"tmdb": {"id": c.get("id")}}}
        for c in cast[:15] if c.get("name")
    ]
    coll = d.get("belongs_to_collection")
    if coll and coll.get("id"):
        # Stash the TMDB *collection* id so franchise grouping stays local-only
        # (collections.rebuild_movie_collections reads this — no Radarr/TMDB needed).
        meta["collection"] = {"name": coll.get("name"), "tmdb_id": coll.get("id"),
                              "position": movie.collection_order}
        providers["tmdb_collection"] = {"id": coll.get("id")}
    meta["trailers"] = P.tmdb_movie_trailers(tmdb_id)
    meta["providers"] = providers
    movie.meta = meta


# ── tv ────────────────────────────────────────────────────────────────────────
def sync_episodes(series: Series, tmdb_id, seasons: list, session: Session) -> int:
    """Upsert Episode STUBS for the whole show from TMDB's season/episode list, so
    'missing' episodes exist (not just files on disk). Preserves file_path/has_file/
    video_codec for episodes already imported; specials (season 0) are tracked but
    left unmonitored. Returns the number of episode rows created."""
    if not tmdb_id or series.id is None:
        return 0
    existing = {(e.season, e.episode): e
                for e in session.exec(select(Episode).where(Episode.series_id == series.id)).all()}
    created = 0
    for srow in (seasons or []):
        sn = srow.get("season_number")
        if sn is None or sn < 0:
            continue
        try:
            sd = P.tmdb_tv_season(tmdb_id, sn)
        except Exception:  # noqa: BLE001
            log.warning("tmdb season fetch failed for tv %s S%s", tmdb_id, sn)
            continue
        for ep in (sd.get("episodes") or []):
            en = ep.get("episode_number")
            if en is None:
                continue
            title = ep.get("name") or None
            air = ep.get("air_date") or None
            row = existing.get((sn, en))
            if row:
                row.title = title or row.title
                if air:
                    row.air_date = air
                session.add(row)
            else:
                session.add(Episode(
                    series_id=series.id, season=sn, episode=en, title=title, air_date=air,
                    monitored=series.monitored and sn >= 1,  # specials off by default
                    has_file=False))
                created += 1
    if created:
        log.info("sync_episodes: %s -> %d new episode stub(s)", series.title, created)
    return created


def enrich_series(series: Series, session: Session) -> None:
    meta = _meta(series)
    providers = meta["providers"]
    tvdb_id = (providers.get("tvdb") or {}).get("id")
    tmdb_id = (providers.get("tmdb") or {}).get("id")

    if not tmdb_id and not tvdb_id:
        results = P.tmdb_search_tv(series.title)
        if results:
            tmdb_id = results[0].get("id")

    # TMDB gives us overview/poster + external_ids -> tvdb id
    if tmdb_id:
        d = P.tmdb_tv(tmdb_id)
        if d:
            series.title = d.get("name") or series.title
            series.overview = d.get("overview") or series.overview
            if d.get("poster_path"):
                series.poster_url = _IMG + d["poster_path"]
            if d.get("first_air_date"):
                yr = d["first_air_date"][:4]
                series.year = int(yr) if yr.isdigit() else series.year
            st = d.get("status", "")
            series.status = "ended" if st in ("Ended", "Canceled") else ("continuing" if st else series.status)
            meta["genres"] = [g.get("name") for g in (d.get("genres") or []) if g.get("name")]
            meta["trailers"] = P.tmdb_tv_trailers(tmdb_id)
            providers["tmdb"] = {"id": tmdb_id, "url": f"https://www.themoviedb.org/tv/{tmdb_id}"}
            ext = d.get("external_ids") or {}
            if ext.get("tvdb_id") and not tvdb_id:
                tvdb_id = ext["tvdb_id"]
            if ext.get("imdb_id"):
                providers["imdb"] = {"id": ext["imdb_id"]}
            # stub the full episode list so missing episodes are tracked
            try:
                sync_episodes(series, tmdb_id, d.get("seasons") or [], session)
            except Exception:  # noqa: BLE001
                log.warning("episode sync failed for %s", series.title, exc_info=True)

    if tvdb_id:
        ts = P.tvdb_series(tvdb_id)
        if ts:
            if not series.overview:
                series.overview = ts.get("overview") or series.overview
            providers["tvdb"] = {"id": tvdb_id, "url": f"https://thetvdb.com/series/{tvdb_id}"}

    meta["providers"] = providers
    series.meta = meta


# ── audiobooks ────────────────────────────────────────────────────────────────
def _wanted_languages() -> set[str]:
    return {x.strip().lower() for x in (settings.METADATA_LANGUAGES or "").split(",") if x.strip()}


def language_ok(value: str | None) -> bool:
    """True when a candidate is in a language we want (or says nothing about its language)."""
    wanted = _wanted_languages()
    if not wanted or not value:
        return True
    v = str(value).strip().lower()
    # Audible says "english"/"spanish"; Audnexus sometimes says "eng"/"spa".
    aliases = {"eng": "english", "spa": "spanish", "ger": "german", "deu": "german",
               "fre": "french", "fra": "french", "ita": "italian", "por": "portuguese"}
    return aliases.get(v, v) in wanted


def enrich_audiobook(book: Audiobook, session: Session) -> None:
    meta = _meta(book)
    providers = meta["providers"]
    asin = book.asin or (providers.get("audible") or {}).get("asin")

    # No ASIN on file? Resolve one from the Audible catalog (title + author) so we
    # can pull a cover + full metadata from Audnexus (reliable, no Google-Books 429).
    if not asin:
        q = " ".join(filter(None, [book.title, book.author_name]))
        cands = P.audible_catalog(keywords=q, num=5) if q.strip() else []
        wrong_language = [c for c in cands if not language_ok(c.get("language"))]
        cands = [c for c in cands if language_ok(c.get("language"))]
        if wrong_language:
            log.info("audiobook %r: skipped %d catalogue match(es) in another language (%s)",
                     book.title, len(wrong_language),
                     ", ".join(sorted({str(c.get("language")) for c in wrong_language})))
        if cands and cands[0].get("asin"):
            asin = cands[0]["asin"]
            book.asin = asin

    if asin:
        d = P.audnexus_book(asin)
        if d and not language_ok(d.get("language")):
            log.warning("audiobook %r: ASIN %s is %s, not applying it", book.title, asin, d.get("language"))
            d = None
        if d:
            book.title = d.get("title") or book.title
            book.subtitle = d.get("subtitle") or book.subtitle
            book.overview = _strip_html(d.get("summary") or d.get("description")) or book.overview
            if d.get("image"):
                book.cover_url = d["image"]
            authors = d.get("authors") or []
            if authors and not book.author_name:
                book.author_name = authors[0].get("name")
            narrators = d.get("narrators") or []
            if narrators and not book.narrator:
                book.narrator = ", ".join(n.get("name") for n in narrators if n.get("name"))
            if d.get("runtimeLengthMin"):
                book.duration_seconds = int(d["runtimeLengthMin"]) * 60
            if d.get("releaseDate"):
                yr = str(d["releaseDate"])[:4]
                book.publish_year = int(yr) if yr.isdigit() else book.publish_year
            series_list = d.get("seriesPrimary") or {}
            if series_list and series_list.get("name"):
                meta.setdefault("collection", {"name": series_list.get("name"),
                                               "position": series_list.get("position")})
            providers["audible"] = {"asin": asin}
            meta["people"] = (
                [{"role": "author", "name": a.get("name"),
                  "providers": {"audible": {"asin": a.get("asin")}}} for a in authors if a.get("name")]
                + [{"role": "narrator", "name": n.get("name"), "providers": {}}
                   for n in narrators if n.get("name")]
            )

    # Fallback synopsis from Google Books / OpenLibrary
    if not book.overview:
        q = " ".join(filter(None, [book.title, book.author_name]))
        gb = P.google_books_search(q)
        if gb:
            vi = (gb[0].get("volumeInfo") or {})
            book.overview = _strip_html(vi.get("description")) or book.overview
            providers["google_books"] = {"volume_id": gb[0].get("id")}
        else:
            ol = P.openlibrary_search(q)
            if ol:
                providers.setdefault("openlibrary", {"work_id": ol[0].get("key")})

    meta["providers"] = providers
    book.meta = meta


# ── public entrypoint ─────────────────────────────────────────────────────────
def refresh_metadata(item_id: int, kind: str, session: Session) -> dict:
    model = {"movie": Movie, "tv": Series, "series": Series, "audiobook": Audiobook}.get(kind)
    if not model:
        raise ValueError(f"unknown kind {kind!r}")
    item = session.get(model, item_id)
    if not item:
        raise ValueError(f"{kind} {item_id} not found")

    if kind == "movie":
        enrich_movie(item, session)
        sidecar_kind = "movie"
    elif kind in ("tv", "series"):
        enrich_series(item, session)
        sidecar_kind = "tv"
    else:
        enrich_audiobook(item, session)
        sidecar_kind = "audiobook"

    if hasattr(item, "last_scanned"):
        item.last_scanned = datetime.utcnow()
    session.add(item)
    session.commit()
    session.refresh(item)

    data = build_metadata_dict(item, sidecar_kind)
    if getattr(item, "folder_path", None):
        try:
            write_sidecar(item.folder_path, data)
        except Exception:
            log.exception("write_sidecar failed for %s %s", kind, item_id)
    return data


# ── bulk enrich (items with no meta file) ─────────────────────────────────────
def _has_providers(row) -> bool:
    provs = (row.meta or {}).get("providers") or {}
    for v in provs.values():
        if isinstance(v, dict) and (v.get("id") or v.get("asin")
                                    or v.get("volume_id") or v.get("work_id")):
            return True
    return False


def _is_enriched(row) -> bool:
    """Already has what we'd display? Then skip the API enrich. A meta file
    (managarr.json / ABS metadata.json / NFO) plus a local cover counts — no need
    to hit providers just because there's no external id. Provider ids ALONE do
    NOT count: sidecar-born rows (the importer writes managarr.json from a shell
    that has ids but no artwork) must still get their art/overview pulled once,
    or they sit in the library looking unmatched."""
    has_art = bool(getattr(row, "poster_url", None) or getattr(row, "cover_url", None))
    has_text = bool(getattr(row, "overview", None))
    return has_art and (has_text or _has_providers(row))


def enrich_missing(session: Session, kinds: list[str] | None = None, delay: float = 0.2) -> dict:
    """Fetch metadata via providers for library items that have no provider ids yet
    (i.e. had no on-disk meta file). Throttled + progress-tracked. refresh_metadata
    writes a managarr.json sidecar, so future scans read it from disk (no re-fetch)."""
    kinds = kinds or ["movie", "tv", "audiobook"]
    models = {"movie": Movie, "tv": Series, "audiobook": Audiobook}
    summary = {}
    for kind in kinds:
        model = models.get(kind)
        if model is None:
            continue
        rows = [r for r in session.exec(select(model)).all() if not _is_enriched(r)]
        ENRICH_PROGRESS[kind] = {"running": True, "total": len(rows), "processed": 0,
                                 "enriched": 0, "started_at": datetime.utcnow().isoformat(),
                                 "error": None}
        processed = enriched = 0
        for r in rows:
            processed += 1
            try:
                refresh_metadata(r.id, kind, session)
                enriched += 1
            except Exception as exc:
                log.debug("enrich %s %s failed: %s", kind, r.id, exc)
            if processed % 5 == 0:
                ENRICH_PROGRESS[kind].update(processed=processed, enriched=enriched)
            if delay:
                time.sleep(delay)
        ENRICH_PROGRESS[kind].update(running=False, processed=processed, enriched=enriched,
                                     finished_at=datetime.utcnow().isoformat())
        summary[kind] = {"processed": processed, "enriched": enriched}
    return summary
