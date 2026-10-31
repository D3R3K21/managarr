"""Collections — movie franchises + audiobook series, with missing-detection.

Two collection kinds share the ``Collection`` / ``CollectionMember`` tables:

- ``kind="movie"`` — a franchise. The owned grouping is built purely from our own
  DB: each movie carries its TMDB *collection* id in ``meta["collection"]`` (set on
  enrich, or bulk-seeded once from Radarr — see ``migrate_from_radarr``). *Missing*
  parts are an optional, throttled TMDB pass.
- ``kind="audiobook"`` — an Audible *series*. The owned grouping is built purely
  from our own DB (``Audiobook.meta["scanned_series"]``, ingested from ABS
  ``metadata.json`` at scan time). *Missing* books are an optional, throttled
  Audible-catalog pass.

Design goals (per operator):
- **No required external integration at runtime.** The normal rebuild reads only
  Managarr's own DB. Radarr/Sonarr/ABS are duplicate managers we're replacing — the
  only thing that touches Radarr is the one-time ``migrate_from_radarr`` bulk import,
  after which Radarr can be deleted.
- **Local-first / no API hammering** on the initial load.
- **Async + progress** — the heavy rebuild runs in a background thread; the UI
  polls ``REBUILD_PROGRESS``.
- **Real-time** — ``relink_after_scan`` re-attaches owned items (and creates new
  local series/franchises from each item's own meta) after every scan, so
  collections stay current without a full rebuild.
- **Correct missing-detection** — a catalog book is *owned* if we already have its
  (series, sequence); this covers duplicate editions AND dramatized adaptations of
  a book number we own, without dropping them.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from ..db import session_scope
from ..models import Audiobook, Collection, CollectionMember, Movie
from ..pipeline import orchestrator
from ..metadata import providers as P
from . import external

log = logging.getLogger("managarr.collections")

# Live progress for the (async) rebuild, surfaced via GET /api/collections/rebuild/status.
REBUILD_PROGRESS: dict = {"running": False}
_REBUILD_LOCK = threading.Lock()


# ── small helpers ─────────────────────────────────────────────────────────────
def levenshtein(a: str, b: str) -> int:
    """Edit distance between two strings (iterative two-row DP)."""
    a, b = a or "", b or ""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + (0 if ca == cb else 1)))
        prev = cur
    return prev[-1]


def _norm(s: Optional[str]) -> str:
    return (s or "").lower().strip()


_SEQ_RE = re.compile(r"#\s*([0-9]+(?:\.[0-9]+)?)\s*$")


def _split_series(raw: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Split a scanned series string ("Children of Time #1") into (name, sequence).

    Sequence is returned as a normalised string ("1", "2.5") or None.
    """
    s = (raw or "").strip()
    if not s:
        return None, None
    m = _SEQ_RE.search(s)
    if m:
        name = s[: m.start()].strip(" #-–·")
        return (name or None), m.group(1)
    return s, None


def _seq_key(seq: Optional[str]) -> Optional[str]:
    """Normalise a sequence so "1", "1.0", 1 compare equal."""
    if seq in (None, ""):
        return None
    try:
        f = float(seq)
        return str(int(f)) if f.is_integer() else str(f)
    except (TypeError, ValueError):
        return str(seq).strip()


def _seq_float(seq: Optional[str]) -> Optional[float]:
    try:
        return float(seq) if seq not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _progress(**kw) -> None:
    REBUILD_PROGRESS.update(kw)


# ── movie franchises (local-first: group by each movie's own TMDB collection id) ─
def _movie_collection_id(m: Movie) -> Optional[str]:
    meta = m.meta or {}
    coll = meta.get("collection") or {}
    cid = coll.get("tmdb_id") or coll.get("id") \
        or ((meta.get("providers") or {}).get("tmdb_collection") or {}).get("id")
    return str(cid) if cid not in (None, "") else None


def rebuild_movie_collections(session: Session) -> dict:
    """Group owned movies into franchise Collections purely from local data — each
    movie's ``meta["collection"]`` TMDB id (set on enrich / seeded by
    ``migrate_from_radarr``). Upserts owned members only; missing parts are managed
    by ``find_missing_movies`` / the migration, so this never deletes them."""
    movies = session.exec(select(Movie)).all()
    _progress(phase="movies", total=len(movies), processed=0, built=0)

    groups: dict[str, dict] = {}   # cid -> {name, movies:[Movie]}
    for i, m in enumerate(movies, 1):
        cid = _movie_collection_id(m)
        if not cid:
            continue
        name = ((m.meta or {}).get("collection") or {}).get("name")
        g = groups.setdefault(cid, {"name": name, "movies": []})
        g["movies"].append(m)
        if name and not g["name"]:
            g["name"] = name
        if i % 100 == 0:
            _progress(processed=i)

    existing = {
        str(((c.meta or {}).get("providers") or {}).get("tmdb_collection", {}).get("id")): c
        for c in session.exec(select(Collection).where(Collection.kind == "movie")).all()
    }

    built = 0
    for cid, g in groups.items():
        coll = existing.get(cid) or Collection(kind="movie", name=g["name"] or f"Collection {cid}")
        coll.name = g["name"] or coll.name
        meta = dict(coll.meta or {})
        meta.setdefault("providers", {})["tmdb_collection"] = {"id": cid}
        coll.meta = meta
        session.add(coll)
        session.flush()
        existing[cid] = coll

        members = {
            str(((mm.meta or {}).get("providers") or {}).get("tmdb", {}).get("id")): mm
            for mm in session.exec(
                select(CollectionMember).where(CollectionMember.collection_id == coll.id)
            ).all()
        }
        for movie in sorted(g["movies"], key=lambda x: (x.year or 9999, x.title or "")):
            pid = str(((movie.meta or {}).get("providers") or {}).get("tmdb", {}).get("id"))
            mm = members.get(pid) or CollectionMember(collection_id=coll.id, title=movie.title)
            mm.title = movie.title
            mm.year = movie.year or mm.year
            mm.position = movie.collection_order if movie.collection_order is not None else mm.position
            mm.item_kind = "movie"
            mm.owned = True
            mm.item_id = movie.id
            mmeta = dict(mm.meta or {})
            mmeta["providers"] = {"tmdb": {"id": pid}}
            if movie.poster_url:
                mmeta["cover_url"] = movie.poster_url
            mm.meta = mmeta
            session.add(mm)
            movie.collection_id = coll.id
            session.add(movie)
        built += 1
    session.commit()

    # Reconcile: reap stale owned members left behind when a movie's collection
    # changed (e.g. a Plex re-match moved it into another franchise, or it lost its
    # collection). Without this, the abandoned owned row keeps its old (often bogus)
    # collection alive past prune. Each owned movie should have exactly one owned
    # member row — in the collection it currently belongs to.
    movie_by_id = {m.id: m for m in movies}
    cid_to_coll_id = {cid: c.id for cid, c in existing.items() if c.id}
    for mm in session.exec(
        select(CollectionMember).where(CollectionMember.item_kind == "movie",
                                       CollectionMember.owned == True)  # noqa: E712
    ).all():
        mv = movie_by_id.get(mm.item_id)
        desired = cid_to_coll_id.get(_movie_collection_id(mv)) if mv else None
        if mm.collection_id != desired:
            session.delete(mm)
    session.commit()

    removed = prune_empty_collections(session, "movie")
    _progress(processed=len(movies), built=built)
    return {"collections": built, "removed_empty": removed}


def find_missing_movies(session: Session, delay: float = 0.3) -> dict:
    """Optional throttled TMDB pass: for each known franchise, enumerate the full
    part list and add the parts we don't own as missing members. Going-forward
    replacement for what ``migrate_from_radarr`` seeds in bulk."""
    movies = session.exec(select(Movie)).all()
    by_tmdb: dict[int, Movie] = {}
    for m in movies:
        tid = ((m.meta or {}).get("providers") or {}).get("tmdb", {}).get("id")
        try:
            if tid is not None:
                by_tmdb[int(tid)] = m
        except (TypeError, ValueError):
            pass

    colls = session.exec(select(Collection).where(Collection.kind == "movie")).all()
    _progress(phase="missing_movies", total=len(colls), processed=0, added=0)
    added = 0
    for i, coll in enumerate(colls, 1):
        cid = ((coll.meta or {}).get("providers") or {}).get("tmdb_collection", {}).get("id")
        if not cid:
            _progress(processed=i)
            continue
        try:
            data = P.tmdb_collection(cid)
        except Exception:
            data = None
        if not data:
            _progress(processed=i)
            continue
        coll.overview = data.get("overview") or coll.overview
        if data.get("poster_path"):
            coll.poster_url = P.TMDB_IMAGE_BASE + data["poster_path"]
        session.add(coll)
        members = {
            str(((mm.meta or {}).get("providers") or {}).get("tmdb", {}).get("id")): mm
            for mm in session.exec(
                select(CollectionMember).where(CollectionMember.collection_id == coll.id)
            ).all()
        }
        parts = sorted(data.get("parts") or [], key=lambda p: p.get("release_date") or "9999")
        for idx, part in enumerate(parts):
            pid = part.get("id")
            if str(pid) in members:
                continue  # already have a member row (owned or missing)
            owner = by_tmdb.get(int(pid)) if pid is not None else None
            rd = part.get("release_date") or None
            mm = CollectionMember(collection_id=coll.id, title=part.get("title") or "",
                                  item_kind="movie", position=float(idx),
                                  year=int(rd[:4]) if rd and rd[:4].isdigit() else None,
                                  release_date=rd, owned=owner is not None,
                                  item_id=owner.id if owner else None)
            mmeta = {"providers": {"tmdb": {"id": pid}}}
            if part.get("poster_path"):
                mmeta["cover_url"] = P.TMDB_IMAGE_BASE + part["poster_path"]
            mm.meta = mmeta
            session.add(mm)
            if not owner:
                added += 1
        session.commit()
        _progress(processed=i, added=added)
        if delay:
            time.sleep(delay)
    return {"missing_added": added}


# ── one-time bulk migration from Radarr (then Radarr can be deleted) ──────────
def migrate_from_radarr(session: Session) -> dict:
    """ONE-TIME bulk import. Radarr already knows every franchise + the full part
    list + posters; we mine that once so we don't TMDB-hammer hundreds of
    collections on first load. This:

    1. stamps each owned movie's ``meta["collection"]`` with its TMDB collection id
       (so the normal, Radarr-free ``rebuild_movie_collections`` can group it), and
    2. seeds full ``Collection`` + ``CollectionMember`` rows (owned + missing +
       posters + overview) directly.

    After running once, Radarr is no longer referenced — collections rebuild from
    local data and new movies self-link via their own meta on enrich/scan.
    """
    if not external.radarr_configured():
        return {"error": "radarr_not_configured"}

    movies = session.exec(select(Movie)).all()
    by_tmdb: dict[int, Movie] = {}
    for m in movies:
        tid = ((m.meta or {}).get("providers") or {}).get("tmdb", {}).get("id")
        try:
            if tid is not None:
                by_tmdb[int(tid)] = m
        except (TypeError, ValueError):
            pass

    rcolls = external.radarr_collections()
    _progress(phase="migrate_radarr", total=len(rcolls), processed=0, built=0, stamped=0)

    existing = {
        str(((c.meta or {}).get("providers") or {}).get("tmdb_collection", {}).get("id")): c
        for c in session.exec(select(Collection).where(Collection.kind == "movie")).all()
    }

    built = stamped = 0
    for i, rc in enumerate(rcolls, 1):
        cid = rc.get("tmdbId")
        parts = rc.get("movies") or []
        owned_parts = [p for p in parts if _is_owned(p.get("tmdbId"), by_tmdb)]
        if cid is None or not owned_parts:
            _progress(processed=i)
            continue

        coll = existing.get(str(cid)) or Collection(kind="movie", name=rc.get("title") or f"Collection {cid}")
        coll.name = rc.get("title") or coll.name
        coll.overview = rc.get("overview") or coll.overview
        coll.poster_url = external.radarr_poster(rc.get("images")) or coll.poster_url
        meta = dict(coll.meta or {})
        meta.setdefault("providers", {})["tmdb_collection"] = {"id": cid}
        coll.meta = meta
        session.add(coll)
        session.flush()
        existing[str(cid)] = coll

        members = {
            str(((mm.meta or {}).get("providers") or {}).get("tmdb", {}).get("id")): mm
            for mm in session.exec(
                select(CollectionMember).where(CollectionMember.collection_id == coll.id)
            ).all()
        }
        ordered = sorted(parts, key=lambda p: (p.get("year") or 9999, p.get("title") or ""))
        for idx, part in enumerate(ordered):
            pid = part.get("tmdbId")
            owner = by_tmdb.get(int(pid)) if pid is not None else None
            mm = members.get(str(pid)) or CollectionMember(collection_id=coll.id, title=part.get("title") or "")
            mm.title = part.get("title") or mm.title
            mm.position = float(idx)
            mm.year = part.get("year") or mm.year
            mm.item_kind = "movie"
            mm.owned = owner is not None
            mm.item_id = owner.id if owner else None
            mmeta = {"providers": {"tmdb": {"id": pid}}}
            poster = external.radarr_poster(part.get("images"))
            if poster:
                mmeta["cover_url"] = poster
            mm.meta = mmeta
            session.add(mm)
            if owner is not None:
                owner.collection_id = coll.id
                owner.collection_order = float(idx)
                ometa = dict(owner.meta or {})
                ometa["collection"] = {"name": coll.name, "tmdb_id": cid, "position": idx}
                owner.meta = ometa
                session.add(owner)
                stamped += 1
        built += 1
        _progress(processed=i, built=built, stamped=stamped)

    session.commit()
    return {"collections": built, "movies_stamped": stamped}


def _is_owned(tmdb_id, by_tmdb: dict) -> bool:
    try:
        return tmdb_id is not None and int(tmdb_id) in by_tmdb
    except (TypeError, ValueError):
        return False


# ── audiobook series (local-first grouping) ───────────────────────────────────
def rebuild_audiobook_series(session: Session) -> dict:
    """Group owned audiobooks into series Collections purely from local data
    (``meta["scanned_series"]`` — ingested from ABS metadata.json). No Audible calls."""
    books = session.exec(select(Audiobook)).all()
    _progress(phase="audiobook_series", total=len(books), processed=0, built=0)

    # series_name(norm) -> (display_name, [(book, seq)])
    groups: dict[str, tuple[str, list]] = {}
    for i, b in enumerate(books, 1):
        raw = (b.meta or {}).get("scanned_series")
        name, seq = _split_series(raw)
        if not name:
            continue
        key = _norm(name)
        groups.setdefault(key, (name, []))[1].append((b, seq))
        if i % 50 == 0:
            _progress(processed=i)

    existing = {
        _norm(c.name): c
        for c in session.exec(select(Collection).where(Collection.kind == "audiobook")).all()
    }

    built = 0
    for key, (name, members) in groups.items():
        coll = existing.get(key) or Collection(kind="audiobook", name=name)
        coll.name = name
        author = next((b.author_name for b, _ in members if b.author_name), None)
        meta = dict(coll.meta or {})
        meta.setdefault("providers", {})
        if author:
            meta["providers"]["author"] = author
        coll.meta = meta
        session.add(coll)
        session.flush()
        existing[key] = coll
        _attach_owned_audiobooks(session, coll, members)
        _prune_false_missing(session, coll, members)
        # Thumbnail: use the earliest-sequence owned book's cover as the series
        # poster (movies get theirs from TMDB/Radarr; audiobook series have none).
        cover = next((b.cover_url for b, _ in
                      sorted(members, key=lambda x: _seq_float(x[1]) if _seq_float(x[1]) is not None else 9999.0)
                      if b.cover_url), None)
        if cover:
            coll.poster_url = cover
            session.add(coll)
        built += 1
    session.commit()
    removed = _prune_empty_audiobook_collections(session)
    _progress(processed=len(books), built=built)
    return {"series": built, "removed_empty": removed}


def _prune_empty_audiobook_collections(session: Session) -> int:
    return prune_empty_collections(session, "audiobook")


def prune_empty_collections(session: Session, kind: str) -> int:
    """Delete collections of ``kind`` with no owned members. For audiobooks these are
    old per-author Audible "Catalog" noise; for movies they're franchises left empty
    when a mismatched member was re-matched away (e.g. a bogus collection created by a
    wrong TMDB match). A library Collections view should reflect what we *have*."""
    from sqlalchemy import delete as sa_delete, update as sa_update
    owned_coll_ids = {
        m.collection_id for m in session.exec(
            select(CollectionMember).where(CollectionMember.owned == True)  # noqa: E712
        ).all()
    }
    empty_ids = [
        c.id for c in session.exec(select(Collection).where(Collection.kind == kind)).all()
        if c.id not in owned_coll_ids
    ]
    if not empty_ids:
        return 0
    # null the item->collection back-refs, then delete children before parents.
    back_model = Movie if kind == "movie" else Audiobook
    session.exec(sa_update(back_model).where(back_model.collection_id.in_(empty_ids))
                 .values(collection_id=None))
    session.exec(sa_delete(CollectionMember).where(CollectionMember.collection_id.in_(empty_ids)))
    session.exec(sa_delete(Collection).where(Collection.id.in_(empty_ids)))
    session.commit()
    return len(empty_ids)


def resync_collection_from_plex(coll_id: int, session: Session) -> dict:
    """Resync a collection's owned movie/TV members from Plex (adopt the match you
    fixed in Plex), then re-group and prune. If the members re-match into other
    franchises this (bogus) collection empties out and is deleted — return
    ``collection_deleted: True`` so the UI can navigate away."""
    from . import plex_sync
    coll = session.get(Collection, coll_id)
    if not coll:
        raise ValueError("collection not found")
    if not plex_sync.plex_configured():
        return {"error": "plex_not_configured"}

    members = session.exec(
        select(CollectionMember).where(CollectionMember.collection_id == coll_id,
                                       CollectionMember.owned == True)  # noqa: E712
    ).all()
    movie_idx = plex_sync._movie_index()
    show_idx = plex_sync._show_index()
    results = []
    for m in members:
        if m.item_kind in ("movie", "tv") and m.item_id:
            r = plex_sync.resync_item(m.item_kind, m.item_id, session,
                                      movie_idx=movie_idx, show_idx=show_idx)
            results.append({"item_id": m.item_id, "kind": m.item_kind,
                            "title": m.title, **r})

    if coll.kind == "movie":
        rebuild_movie_collections(session)   # re-groups + prunes empties
    else:
        rebuild_audiobook_series(session)

    deleted = session.get(Collection, coll_id) is None
    return {"resynced": results, "collection_deleted": deleted,
            "unmatched": [r for r in results if not r.get("ok")]}


def _prune_false_missing(session: Session, coll: Collection, members: list) -> None:
    """Delete 'missing' members of an audiobook series that we actually own — by
    (sequence) / asin / title. Removes duplicate editions and dramatized
    adaptations of an owned book number (e.g. A Court of Thorns and Roses) that an
    earlier, title-only missing pass wrongly flagged. Pure-local, no API."""
    owned_seqs = {_seq_key(seq) for _, seq in members if _seq_key(seq)}
    owned_asins = {b.asin.upper() for b, _ in members if b.asin}
    owned_titles = {_norm(b.title) for b, _ in members}
    for mm in session.exec(
        select(CollectionMember).where(CollectionMember.collection_id == coll.id,
                                       CollectionMember.owned == False)  # noqa: E712
    ).all():
        mseq = _seq_key(str(mm.position)) if mm.position is not None else None
        masin = (((mm.meta or {}).get("providers") or {}).get("audible", {}).get("asin") or "").upper()
        if (mseq and mseq in owned_seqs) or (masin and masin in owned_asins) \
                or (_norm(mm.title) in owned_titles):
            session.delete(mm)


def _attach_owned_audiobooks(session: Session, coll: Collection, members: list) -> None:
    """Upsert owned CollectionMembers for an audiobook series + link the books.
    ``members`` is a list of (Audiobook, seq_str)."""
    existing = {
        ((m.meta or {}).get("providers") or {}).get("audible", {}).get("asin")
        or _norm(m.title): m
        for m in session.exec(
            select(CollectionMember).where(CollectionMember.collection_id == coll.id)
        ).all()
    }
    for book, seq in members:
        kkey = book.asin or _norm(book.title)
        mm = existing.get(kkey) or CollectionMember(collection_id=coll.id, title=book.title)
        mm.title = book.title
        mm.position = _seq_float(seq)
        mm.year = book.publish_year or mm.year
        mm.item_kind = "audiobook"
        mm.owned = True
        mm.item_id = book.id
        mmeta = dict(mm.meta or {})
        mmeta["providers"] = {"audible": {"asin": book.asin}} if book.asin else mmeta.get("providers", {})
        if book.cover_url:
            mmeta["cover_url"] = book.cover_url
        mm.meta = mmeta
        session.add(mm)
        # durable link on the book
        book.collection_id = coll.id
        book.series_position = _seq_float(seq)
        session.add(book)


# ── missing-detection (optional, throttled Audible pass) ──────────────────────
def find_missing_audiobooks(session: Session, delay: float = 0.4) -> dict:
    """For each owned author, enumerate the Audible catalog and add *missing*
    series members. A catalog book is owned (not missing) when we already have its
    (series, sequence) — so duplicate editions and dramatized adaptations of an
    owned book number are NOT flagged missing. Throttled + progress-tracked."""
    books = session.exec(select(Audiobook)).all()
    authors: dict[str, list[Audiobook]] = {}
    for b in books:
        if b.author_name:
            authors.setdefault(b.author_name, []).append(b)

    # owned lookups
    owned_seq: set[tuple[str, str]] = set()   # (series_norm, seq_key)
    owned_asins: set[str] = set()
    owned_titles: set[str] = set()
    for b in books:
        if b.asin:
            owned_asins.add(b.asin.upper())
        if b.title:
            owned_titles.add(_norm(b.title))
        sname, sseq = _split_series((b.meta or {}).get("scanned_series"))
        sk = _seq_key(sseq)
        if sname and sk:
            owned_seq.add((_norm(sname), sk))

    existing = {
        _norm(c.name): c
        for c in session.exec(select(Collection).where(Collection.kind == "audiobook")).all()
    }

    _progress(phase="missing", total=len(authors), processed=0, added=0)
    added = 0
    for i, (author, _owned_books) in enumerate(authors.items(), 1):
        try:
            products = P.audible_catalog(author=author, num=50) or []
        except Exception as exc:
            log.debug("audible_catalog(%s) failed: %s", author, exc)
            products = []

        groups: dict[str, list[dict]] = {}
        for p in products:
            sname = p.get("seriesName")
            if not sname:
                continue  # standalone — not a series gap we track
            groups.setdefault(sname, []).append(p)

        for series_name, prods in groups.items():
            skey = _norm(series_name)
            coll = existing.get(skey)
            if not coll:
                # only surface missing for series we actually own something in
                continue
            members = {
                ((m.meta or {}).get("providers") or {}).get("audible", {}).get("asin"): m
                for m in session.exec(
                    select(CollectionMember).where(CollectionMember.collection_id == coll.id)
                ).all()
            }
            for p in sorted(prods, key=lambda x: _seq_float(x.get("seriesPosition")) or 0):
                asin = (p.get("asin") or "")
                seqk = _seq_key(p.get("seriesPosition"))
                title_n = _norm(p.get("title"))
                owned = (
                    (seqk and (skey, seqk) in owned_seq)
                    or (asin.upper() in owned_asins)
                    or (title_n and any(levenshtein(title_n, t) <= 2 for t in owned_titles))
                )
                if owned or not asin:
                    continue
                mm = members.get(asin)
                if mm and not mm.owned:
                    continue  # already recorded as missing
                if not mm:
                    mm = CollectionMember(collection_id=coll.id, title=p.get("title") or "")
                    added += 1
                mm.title = p.get("title") or mm.title
                mm.position = _seq_float(p.get("seriesPosition"))
                yr = p.get("publishedYear")
                mm.year = int(yr) if yr and str(yr).isdigit() else mm.year
                mm.item_kind = "audiobook"
                mm.owned = False
                mm.meta = {"providers": {"audible": {"asin": asin}}, "cover_url": p.get("cover"),
                           "author": author, "series": series_name}
                session.add(mm)
        session.commit()
        _progress(processed=i, added=added)
        if delay:
            time.sleep(delay)
    return {"missing_added": added}


# ── real-time: keep collections current after a scan (no API) ─────────────────
def relink_after_scan(session: Session) -> dict:
    """Cheap, API-free pass run after a library scan: rebuild local audiobook
    series (so new books appear instantly) and re-evaluate movie ownership against
    existing franchises. New movie franchises are only discovered by the full
    Radarr rebuild."""
    out = {}
    try:
        out["audiobook_series"] = rebuild_audiobook_series(session)
    except Exception:
        log.exception("relink audiobook series failed")
    try:
        out["movies"] = relink_movie_owned(session)
    except Exception:
        log.exception("relink movie owned failed")
    return out


def relink_movie_owned(session: Session) -> dict:
    """Re-evaluate owned/owner for existing movie collection members against the
    current library (by TMDB id). No Radarr call — links movies that were added
    into franchises we already know about."""
    movies = session.exec(select(Movie)).all()
    by_tmdb: dict[int, Movie] = {}
    for m in movies:
        tid = ((m.meta or {}).get("providers") or {}).get("tmdb", {}).get("id")
        try:
            if tid is not None:
                by_tmdb[int(tid)] = m
        except (TypeError, ValueError):
            pass

    relinked = 0
    coll_names = {c.id: c.name for c in session.exec(select(Collection).where(Collection.kind == "movie")).all()}
    for mm in session.exec(select(CollectionMember).where(CollectionMember.item_kind == "movie")).all():
        pid = ((mm.meta or {}).get("providers") or {}).get("tmdb", {}).get("id")
        owner = by_tmdb.get(int(pid)) if str(pid).isdigit() else None
        new_owned = owner is not None
        if new_owned != mm.owned or (owner and mm.item_id != owner.id):
            mm.owned = new_owned
            mm.item_id = owner.id if owner else None
            session.add(mm)
            if owner is not None:
                owner.collection_id = mm.collection_id
                owner.collection_order = mm.position
                ometa = dict(owner.meta or {})
                ometa["collection"] = {"name": coll_names.get(mm.collection_id), "position": mm.position}
                owner.meta = ometa
                session.add(owner)
            relinked += 1
    session.commit()
    return {"relinked": relinked}


# ── async orchestration ───────────────────────────────────────────────────────
def start_rebuild(kind: Optional[str] = None, include_missing: bool = False) -> bool:
    """Kick off a background rebuild (own-DB only). Returns False if one is already
    running. The one-time Radarr import is a separate, synchronous endpoint."""
    if not _REBUILD_LOCK.acquire(blocking=False):
        return False
    REBUILD_PROGRESS.clear()
    REBUILD_PROGRESS.update(running=True, phase="starting", processed=0, total=0,
                            built=0, error=None, kind=kind or "all",
                            include_missing=include_missing,
                            started_at=datetime.utcnow().isoformat())
    t = threading.Thread(target=_run_rebuild, args=(kind, include_missing), daemon=True)
    t.start()
    return True


def _run_rebuild(kind: Optional[str], include_missing: bool) -> None:
    try:
        with session_scope() as session:
            result = {}
            if kind in (None, "movie"):
                result["movies"] = rebuild_movie_collections(session)
                if include_missing:
                    result["missing_movies"] = find_missing_movies(session)
            if kind in (None, "audiobook"):
                result["audiobook_series"] = rebuild_audiobook_series(session)
                if include_missing:
                    result["missing_audiobooks"] = find_missing_audiobooks(session)
            _progress(result=result)
    except Exception as exc:
        log.exception("rebuild failed")
        _progress(error=str(exc))
    finally:
        _progress(running=False, phase="done", finished_at=datetime.utcnow().isoformat())
        _REBUILD_LOCK.release()


# ── queries ───────────────────────────────────────────────────────────────────
def by_author(author_name_or_id, session: Session) -> dict:
    """Owned audiobooks + missing catalog titles for one author (series+seq dedup)."""
    name = str(author_name_or_id)
    if str(author_name_or_id).isdigit():
        from ..models import Person
        person = session.get(Person, int(author_name_or_id))
        if person:
            name = person.name

    owned = session.exec(
        select(Audiobook).where(Audiobook.author_name == name).order_by(Audiobook.title)
    ).all()
    owned_seq: set[tuple[str, str]] = set()
    owned_asins = {b.asin.upper() for b in owned if b.asin}
    owned_titles = {_norm(b.title) for b in owned if b.title}
    for b in owned:
        sname, sseq = _split_series((b.meta or {}).get("scanned_series"))
        sk = _seq_key(sseq)
        if sname and sk:
            owned_seq.add((_norm(sname), sk))

    try:
        products = P.audible_catalog(author=name, num=50) or []
    except Exception:
        products = []

    missing = []
    for p in products:
        ft = (p.get("format_type") or "").lower()
        if ft and ft != "unabridged":
            continue
        skey = _norm(p.get("seriesName"))
        seqk = _seq_key(p.get("seriesPosition"))
        asin = (p.get("asin") or "").upper()
        title_n = _norm(p.get("title"))
        if (skey and seqk and (skey, seqk) in owned_seq) or asin in owned_asins \
                or (title_n and any(levenshtein(title_n, t) <= 2 for t in owned_titles)):
            continue
        missing.append({
            "asin": p.get("asin"), "title": p.get("title"), "subtitle": p.get("subtitle"),
            "cover": p.get("cover"), "series": p.get("seriesName"),
            "position": p.get("seriesPosition"), "year": p.get("publishedYear"),
        })
    return {"author": name, "owned": owned, "missing": missing}


def missing(session: Session, kind: Optional[str] = None) -> list[CollectionMember]:
    rows = session.exec(select(CollectionMember).where(CollectionMember.owned == False)).all()  # noqa: E712
    if kind:
        rows = [r for r in rows if (r.item_kind or "") == kind]
    return rows


def grab_missing(member_id: int, session: Session):
    member = session.get(CollectionMember, member_id)
    if not member:
        raise ValueError(f"collection member {member_id} not found")
    coll = session.get(Collection, member.collection_id)
    kind = member.item_kind or (coll.kind if coll else "movie")
    meta = member.meta or {}
    if kind == "audiobook":
        author = meta.get("author") or (((coll.meta or {}).get("providers") or {}).get("author") if coll else "")
        series = meta.get("series") or (coll.name if coll else "")
        save_path = orchestrator.build_save_path("audiobook", title=member.title, author=author or "", series=series or "")
        return orchestrator.auto_grab("audiobook", member.title, save_path, author=author or "", source="missing")
    save_path = orchestrator.build_save_path("movie", title=member.title, year=member.year)
    query = f"{member.title} {member.year}" if member.year else member.title
    return orchestrator.auto_grab("movie", query, save_path, source="missing")
