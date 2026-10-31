"""Library browse / detail / monitor / refresh-metadata + root folders + dir browse."""
from __future__ import annotations

import logging
import os
import shutil

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from ..auth import require_admin, require_user
from ..config import settings
from ..db import get_session
from ..metadata.aggregator import refresh_metadata
from ..models import Audiobook, CollectionMember, Episode, M4bJob, Movie, RootFolder, Series
from ..pipeline import download_queue

router = APIRouter()
log = logging.getLogger("managarr.library")
_KINDS = {"movie", "tv", "audiobook"}


def _delete_item_folder(folder: str, session: Session) -> bool:
    """Safely remove an item's on-disk folder — only when it sits strictly INSIDE a
    known library root (never an arbitrary path or a root itself). Returns True if a
    folder was actually removed."""
    if not folder:
        return False
    p = os.path.abspath(folder)
    roots = [settings.MOVIES_PATH, settings.NEW_MOVIES_PATH, settings.TV_PATH, settings.AUDIOBOOKS_PATH]
    roots += [r.path for r in session.exec(select(RootFolder)).all()]
    roots = [os.path.abspath(r) for r in roots if r]
    if not any(p != r and p.startswith(r + os.sep) for r in roots):
        log.warning("refusing to delete folder outside library roots: %s", p)
        return False
    if os.path.isdir(p):
        shutil.rmtree(p, ignore_errors=True)
        log.info("deleted on-disk folder %s", p)
        return True
    return False
_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


_TMDB_IMG = "https://image.tmdb.org/t/p/w342"


@router.get("/match/search")
def match_search(kind: str, q: str, user: str = Depends(require_user)):
    """Search a metadata provider for the correct match (used by 'Fix match')."""
    from ..metadata import providers as P
    out = []
    if kind == "movie":
        for r in (P.tmdb_search_movie(q) or [])[:12]:
            out.append({"provider": "tmdb", "id": str(r.get("id")), "title": r.get("title"),
                        "year": (str(r.get("release_date") or "")[:4] or None),
                        "poster": (_TMDB_IMG + r["poster_path"]) if r.get("poster_path") else None,
                        "overview": r.get("overview")})
    elif kind == "tv":
        for r in (P.tmdb_search_tv(q) or [])[:12]:
            out.append({"provider": "tmdb", "id": str(r.get("id")), "title": r.get("name"),
                        "year": (str(r.get("first_air_date") or "")[:4] or None),
                        "poster": (_TMDB_IMG + r["poster_path"]) if r.get("poster_path") else None,
                        "overview": r.get("overview")})
    elif kind == "audiobook":
        for r in (P.audible_catalog(keywords=q, num=12) or [])[:12]:
            authors = ", ".join(a.get("name", "") for a in (r.get("authors") or []))
            out.append({"provider": "audible", "id": r.get("asin"), "title": r.get("title"),
                        "year": r.get("publishedYear"), "poster": r.get("cover"),
                        "overview": (authors + (" — " + r["seriesName"] if r.get("seriesName") else "")).strip(" —")})
    else:
        raise HTTPException(400, "kind must be movie|tv|audiobook")
    return [r for r in out if r["id"]]


@router.post("/{kind}/{item_id}/match")
def fix_match(kind: str, item_id: int, body: dict, session: Session = Depends(get_session),
              user: str = Depends(require_user)):
    """Pin the chosen provider id and re-pull metadata from it."""
    model = {"movie": Movie, "movies": Movie, "tv": Series,
             "audiobook": Audiobook, "audiobooks": Audiobook}.get(kind)
    if not model:
        raise HTTPException(400, "kind must be movies|tv|audiobooks")
    kind = "audiobook" if model is Audiobook else ("tv" if model is Series else "movie")
    item = session.get(model, item_id)
    if not item:
        raise HTTPException(404, f"{kind} not found")
    provider = body.get("provider") or ("audible" if kind == "audiobook" else "tmdb")
    pid = str(body.get("id") or "").strip()
    if not pid:
        raise HTTPException(400, "id required")
    meta = dict(item.meta or {})
    provs = dict(meta.get("providers") or {})
    provs[provider] = {"asin": pid} if provider == "audible" else {"id": pid}
    meta["providers"] = provs
    meta["meta_source"] = "fix_match"
    item.meta = meta
    if kind == "audiobook" and provider == "audible":
        item.asin = pid
    session.add(item)
    session.commit()
    return refresh_metadata(item_id, kind, session)


@router.post("/{kind}/{item_id}/resync-plex")
def resync_plex_item(kind: str, item_id: int, session: Session = Depends(get_session),
                     user: str = Depends(require_user)):
    """Adopt the match Plex shows for this item (reads Plex's tmdb/imdb/tvdb Guid for
    the same file, pins it, and re-pulls metadata). Movies + TV only."""
    from ..services import plex_sync
    norm = {"movies": "movie", "movie": "movie", "tv": "tv", "series": "tv"}.get(kind)
    if not norm:
        raise HTTPException(400, "resync-plex supports movie|tv only")
    return plex_sync.resync_item(norm, item_id, session)


@router.post("/resync-plex")
def resync_plex_all(user: str = Depends(require_user)):
    """Bulk: resync every movie/series from Plex in the background. Poll
    /resync-plex/status."""
    import threading
    from ..services import plex_sync
    if plex_sync.RESYNC_PROGRESS.get("running"):
        return {"started": False, "reason": "already_running", "progress": plex_sync.RESYNC_PROGRESS}
    if not plex_sync.plex_configured():
        raise HTTPException(400, "PLEX_URL/PLEX_TOKEN not configured")

    def _run():
        from ..db import session_scope
        try:
            with session_scope() as s:
                plex_sync.resync_all(s)
        except Exception:
            import logging
            logging.getLogger("managarr.plex_sync").exception("bulk resync failed")
            plex_sync.RESYNC_PROGRESS.update(running=False, error="bulk resync failed")

    threading.Thread(target=_run, daemon=True).start()
    return {"started": True, "progress": plex_sync.RESYNC_PROGRESS}


@router.get("/resync-plex/status")
def resync_plex_status():
    from ..services import plex_sync
    return plex_sync.RESYNC_PROGRESS


@router.post("/sync-audible")
def sync_audible(session: Session = Depends(get_session), user: str = Depends(require_admin)):
    """Trigger an immediate Audible library sync (fetches new books from Libation,
    reconciles with DB, logs events). Runs in background."""
    import threading
    from ..services import audible_sync

    if audible_sync.SYNC_PROGRESS.get("running"):
        return {"started": False, "reason": "already_running", "progress": audible_sync.SYNC_PROGRESS}

    if not audible_sync.audible_configured():
        raise HTTPException(400, "LIBATION_DB_PATH not configured or Libation DB not found")

    def _run():
        from ..db import session_scope
        try:
            with session_scope() as s:
                audible_sync.sync(s)
        except Exception:
            import logging
            logging.getLogger("managarr.audible_sync").exception("Audible sync failed")
            audible_sync.SYNC_PROGRESS.update(running=False, error="sync failed (see logs)")

    threading.Thread(target=_run, daemon=True).start()
    return {"started": True, "progress": audible_sync.SYNC_PROGRESS}


@router.get("/sync-audible/status")
def sync_audible_status():
    from ..services import audible_sync
    return audible_sync.SYNC_PROGRESS


def _detach_members(kind: str, item_id: int, session: Session) -> None:
    for mem in session.exec(
        select(CollectionMember).where(CollectionMember.item_kind == kind,
                                       CollectionMember.item_id == item_id)
    ).all():
        mem.owned = False
        mem.item_id = None
        session.add(mem)


@router.delete("/movies/{movie_id}")
def delete_movie(movie_id: int, delete_files: bool = False,
                 session: Session = Depends(get_session),
                 user: str = Depends(require_admin)):
    """Remove a movie's DB entry. With ``delete_files`` the on-disk folder (and any
    in-flight download's data) is removed too; otherwise files are left and a rescan
    re-adds it."""
    m = session.get(Movie, movie_id)
    if not m:
        raise HTTPException(404, "movie not found")
    files_deleted = _delete_item_folder(m.folder_path, session) if delete_files else False
    _detach_members("movie", movie_id, session)
    download_queue.delete_for_item(session, "movie", movie_id, delete_files=delete_files)
    session.delete(m)
    session.commit()
    return {"deleted": movie_id, "kind": "movie", "files_deleted": files_deleted}


@router.delete("/tv/{series_id}")
def delete_series(series_id: int, delete_files: bool = False,
                  session: Session = Depends(get_session),
                  user: str = Depends(require_admin)):
    """Remove a series' DB entry + its episodes. With ``delete_files`` the on-disk
    series folder (all seasons) and any in-flight download are removed too."""
    s = session.get(Series, series_id)
    if not s:
        raise HTTPException(404, "series not found")
    files_deleted = _delete_item_folder(s.folder_path, session) if delete_files else False
    for ep in session.exec(select(Episode).where(Episode.series_id == series_id)).all():
        session.delete(ep)
    _detach_members("tv", series_id, session)
    download_queue.delete_for_item(session, "tv", series_id, delete_files=delete_files)
    session.delete(s)
    session.commit()
    return {"deleted": series_id, "kind": "tv", "files_deleted": files_deleted}


@router.delete("/audiobooks/{audiobook_id}")
def delete_audiobook(audiobook_id: int, delete_files: bool = False,
                     session: Session = Depends(get_session),
                     user: str = Depends(require_admin)):
    """Remove an audiobook's DB entry. With ``delete_files`` the on-disk folder and
    any in-flight download are removed too; otherwise files are left."""
    b = session.get(Audiobook, audiobook_id)
    if not b:
        raise HTTPException(404, "audiobook not found")
    files_deleted = _delete_item_folder(b.folder_path, session) if delete_files else False
    _detach_members("audiobook", audiobook_id, session)
    download_queue.delete_for_item(session, "audiobook", audiobook_id, delete_files=delete_files)
    # clear FK-referencing m4b conversion jobs, else the delete violates the constraint
    for job in session.exec(select(M4bJob).where(M4bJob.audiobook_id == audiobook_id)).all():
        session.delete(job)
    session.delete(b)
    session.commit()
    return {"deleted": audiobook_id, "kind": "audiobook", "files_deleted": files_deleted}


@router.get("/poster")
def poster(path: str, session: Session = Depends(get_session)):
    """Serve an on-disk poster/cover. Unauthenticated (an <img> tag can't send the
    bearer token; the public host is gated by Cloudflare Access). Path is validated
    to be inside a known library root and an image, to prevent arbitrary file reads."""
    p = os.path.abspath(path)
    roots = [settings.MOVIES_PATH, settings.NEW_MOVIES_PATH, settings.TV_PATH, settings.AUDIOBOOKS_PATH]
    roots += [r.path for r in session.exec(select(RootFolder)).all()]
    roots = [os.path.abspath(r) for r in roots if r]
    if not any(p == r or p.startswith(r + os.sep) for r in roots):
        raise HTTPException(403, "outside library roots")
    if not os.path.isfile(p) or os.path.splitext(p)[1].lower() not in _IMG_EXTS:
        raise HTTPException(404, "not found")
    return FileResponse(p)


# ── movies ────────────────────────────────────────────────────────────────────
@router.get("/movies")
def list_movies(session: Session = Depends(get_session)):
    return session.exec(select(Movie).order_by(Movie.title)).all()


@router.get("/movies/{movie_id}")
def get_movie(movie_id: int, session: Session = Depends(get_session)):
    m = session.get(Movie, movie_id)
    if not m:
        raise HTTPException(404, "movie not found")
    return m


@router.patch("/movies/{movie_id}/monitored")
def set_movie_monitored(movie_id: int, body: dict, session: Session = Depends(get_session),
                        user: str = Depends(require_user)):
    m = session.get(Movie, movie_id)
    if not m:
        raise HTTPException(404, "movie not found")
    m.monitored = bool(body.get("monitored", True))
    session.add(m)
    session.commit()
    return {"id": m.id, "monitored": m.monitored}


@router.post("/movies/{movie_id}/refresh-metadata")
def refresh_movie(movie_id: int, session: Session = Depends(get_session),
                  user: str = Depends(require_user)):
    try:
        return refresh_metadata(movie_id, "movie", session)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/movies/{movie_id}/upgrade")
def upgrade_movie(movie_id: int, body: dict | None = None,
                  session: Session = Depends(get_session),
                  user: str = Depends(require_user)):
    """Replace this movie's file with a better release.

    Body (all optional):
      release  — a specific normalized release to upgrade to (from /indexers/search);
                 omit to auto-pick the best profile-passing release.
      monitor  — if nothing qualifies now, flag the movie to be retried automatically
                 by the scheduled upgrade search (set it when only a cam/ts exists today).
      force    — accept ``release`` even if it isn't scored strictly better (manual override).

    Returns {status}: grabbed | monitoring | no_release | error. The old file is
    removed only after the new one finishes downloading and imports."""
    from ..pipeline import upgrade
    m = session.get(Movie, movie_id)
    if not m:
        raise HTTPException(404, "movie not found")
    body = body or {}
    return upgrade.start_movie_upgrade(
        m, session, release=body.get("release"),
        monitor=bool(body.get("monitor")), force=bool(body.get("force")))


@router.delete("/movies/{movie_id}/upgrade")
def cancel_upgrade_movie(movie_id: int, session: Session = Depends(get_session),
                         user: str = Depends(require_user)):
    """Stop monitoring for an upgrade / clear a pending-upgrade marker. Does not stop
    an in-flight download (drop that from the Activity queue if you want it gone)."""
    from ..pipeline import upgrade
    m = session.get(Movie, movie_id)
    if not m:
        raise HTTPException(404, "movie not found")
    return upgrade.cancel_movie_upgrade(m, session)


# ── tv ────────────────────────────────────────────────────────────────────────
@router.get("/tv")
def list_series(session: Session = Depends(get_session)):
    return session.exec(select(Series).order_by(Series.title)).all()


@router.get("/tv/{series_id}")
def get_series(series_id: int, session: Session = Depends(get_session)):
    s = session.get(Series, series_id)
    if not s:
        raise HTTPException(404, "series not found")
    episodes = session.exec(
        select(Episode).where(Episode.series_id == series_id).order_by(Episode.season, Episode.episode)
    ).all()
    return {"series": s, "episodes": episodes}


@router.patch("/tv/{series_id}/monitored")
def set_series_monitored(series_id: int, body: dict, session: Session = Depends(get_session),
                         user: str = Depends(require_user)):
    s = session.get(Series, series_id)
    if not s:
        raise HTTPException(404, "series not found")
    s.monitored = bool(body.get("monitored", True))
    session.add(s)
    session.commit()
    return {"id": s.id, "monitored": s.monitored}


@router.post("/tv/{series_id}/refresh-metadata")
def refresh_series(series_id: int, session: Session = Depends(get_session),
                   user: str = Depends(require_user)):
    try:
        return refresh_metadata(series_id, "tv", session)
    except ValueError as e:
        raise HTTPException(404, str(e))


# ── audiobooks ────────────────────────────────────────────────────────────────
@router.get("/audiobooks")
def list_audiobooks(session: Session = Depends(get_session)):
    return session.exec(select(Audiobook).order_by(Audiobook.author_name, Audiobook.title)).all()


@router.get("/audiobooks/by-author")
def audiobooks_by_author(session: Session = Depends(get_session)):
    books = session.exec(select(Audiobook).order_by(Audiobook.author_name, Audiobook.title)).all()
    grouped: dict[str, list] = {}
    for b in books:
        grouped.setdefault(b.author_name or "Unknown", []).append(b)
    return [{"author": a, "books": bs} for a, bs in grouped.items()]


@router.get("/audiobooks/{book_id}")
def get_audiobook(book_id: int, session: Session = Depends(get_session)):
    b = session.get(Audiobook, book_id)
    if not b:
        raise HTTPException(404, "audiobook not found")
    return b


@router.patch("/audiobooks/{book_id}/monitored")
def set_audiobook_monitored(book_id: int, body: dict, session: Session = Depends(get_session),
                            user: str = Depends(require_user)):
    b = session.get(Audiobook, book_id)
    if not b:
        raise HTTPException(404, "audiobook not found")
    b.monitored = bool(body.get("monitored", True))
    session.add(b)
    session.commit()
    return {"id": b.id, "monitored": b.monitored}


@router.post("/audiobooks/{book_id}/refresh-metadata")
def refresh_audiobook(book_id: int, session: Session = Depends(get_session),
                      user: str = Depends(require_user)):
    try:
        return refresh_metadata(book_id, "audiobook", session)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/audiobooks/m4b/jobs")
def m4b_jobs(session: Session = Depends(get_session)):
    """Recent MP3->m4b conversion jobs (status + progress for the UI to poll)."""
    from ..services import m4b
    return m4b.jobs(session)


@router.post("/audiobooks/{book_id}/convert-m4b")
def convert_audiobook_m4b(book_id: int, session: Session = Depends(get_session),
                          user: str = Depends(require_user)):
    """Combine a multi-file (MP3/etc) audiobook into one chapterized .m4b in the
    background. Source files are kept. Poll /audiobooks/m4b/jobs for progress."""
    from ..services import m4b
    existing = m4b.active_for(session, book_id)
    if existing:
        return {"started": False, "reason": "already_running", "job": existing}
    try:
        job = m4b.start_convert(book_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"started": True, "job": job}


# ── root folders (libraries) ──────────────────────────────────────────────────
class RootFolderBody(BaseModel):
    path: str
    media_kind: str


@router.get("/rootfolders")
def list_rootfolders(session: Session = Depends(get_session)):
    rows = session.exec(select(RootFolder).order_by(RootFolder.media_kind, RootFolder.path)).all()
    return [{"id": r.id, "path": r.path, "media_kind": r.media_kind,
             "exists": os.path.isdir(r.path)} for r in rows]


@router.post("/rootfolders")
def add_rootfolder(body: RootFolderBody, session: Session = Depends(get_session),
                   user: str = Depends(require_admin)):
    if body.media_kind not in _KINDS:
        raise HTTPException(400, f"media_kind must be one of {sorted(_KINDS)}")
    path = os.path.abspath((body.path or "").rstrip("/") or "/")
    if not os.path.isdir(path):
        raise HTTPException(400, f"not a directory: {path}")
    dup = session.exec(select(RootFolder).where(
        RootFolder.path == path, RootFolder.media_kind == body.media_kind)).first()
    if dup:
        raise HTTPException(409, "already exists")
    rf = RootFolder(path=path, media_kind=body.media_kind)
    session.add(rf)
    session.commit()
    session.refresh(rf)
    return {"id": rf.id, "path": rf.path, "media_kind": rf.media_kind, "exists": True}


@router.delete("/rootfolders/{rf_id}")
def delete_rootfolder(rf_id: int, session: Session = Depends(get_session),
                      user: str = Depends(require_admin)):
    rf = session.get(RootFolder, rf_id)
    if rf:
        session.delete(rf)
        session.commit()
    return {"ok": True}


# ── directory browse (file picker for the libraries page) ─────────────────────
@router.get("/browse")
def browse(path: str = "/", session: Session = Depends(get_session),
           user: str = Depends(require_user)):
    target = os.path.abspath(path or "/")
    if not os.path.isdir(target):
        raise HTTPException(404, f"not a directory: {target}")
    try:
        entries = sorted(os.listdir(target))
    except PermissionError:
        raise HTTPException(403, "permission denied")
    dirs = [{"name": n, "path": os.path.join(target, n)}
            for n in entries if os.path.isdir(os.path.join(target, n))]
    parent = None if target == "/" else os.path.dirname(target)
    return {"path": target, "parent": parent, "dirs": dirs}
