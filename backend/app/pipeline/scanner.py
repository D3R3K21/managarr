"""Filesystem library scanners.

Walk the configured RootFolders (or the env library paths as a fallback), upsert
rows keyed by folder_path, and detect basic issues. Designed to be fast over NFS:

- **No per-file ffprobe** by default (``SCAN_PROBE_CODEC``) — codec detection is
  slow over NFS and only needed for transcode, which probes its own candidates.
- **Batched commits** (``SCAN_BATCH``) so rows appear as the scan runs and a crash
  doesn't lose everything.
- **Live progress** in ``PROGRESS[kind] = {running, total, processed, found}`` so the
  UI can show scanned/total.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from datetime import datetime
from urllib.parse import quote

from sqlmodel import Session, select

from ..config import settings
from ..metadata import sidecar
from ..models import Audiobook, Episode, M4bJob, Movie, RootFolder, Series

log = logging.getLogger("managarr.scanner")

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".m2ts"}
AUDIO_EXTS = {".m4b", ".mp3", ".m4a", ".flac", ".ogg", ".opus"}
IGNORE_MARKER = ".ignore"  # Audiobookshelf's per-folder "skip me" marker
_YEAR_RE = re.compile(r"^(.+?)\s*\((\d{4})\)\s*$")
_EP_RE = re.compile(r"[Ss](\d+)[Ee](\d+)")
_SEASON_RE = re.compile(r"[Ss]eason\s*(\d+)|\bS(\d+)\b", re.I)

# Live progress, read by GET /api/scan/status.
PROGRESS: dict[str, dict] = {}


def _set_progress(kind: str, **kw) -> None:
    cur = PROGRESS.get(kind, {})
    cur.update(kw)
    PROGRESS[kind] = cur


def _roots(session: Session, kind: str) -> list[str]:
    """Configured RootFolders for a kind, else the single env path fallback.

    For movies we ALSO always scan the New Movies staging library
    (``NEW_MOVIES_PATH``): downloads land and seed there until they're watched in
    Plex and the watched-mover promotes them to ``MOVIES_PATH``. Scanning it makes
    those staged movies count as owned — so collections show complete and the
    auto-download queue doesn't re-grab a movie you already have. The mover updates
    each row's ``folder_path`` on promotion, so no duplicate rows result."""
    rows = session.exec(select(RootFolder).where(RootFolder.media_kind == kind)).all()
    paths = [r.path for r in rows]
    if not paths:
        env = {"movie": settings.MOVIES_PATH, "tv": settings.TV_PATH,
               "audiobook": settings.AUDIOBOOKS_PATH}.get(kind)
        if env:
            paths = [env]
    if kind == "movie" and settings.NEW_MOVIES_PATH:
        paths.append(settings.NEW_MOVIES_PATH)
    # de-dup (preserve order) and drop non-existent dirs
    out: list[str] = []
    seen: set[str] = set()
    for p in paths:
        if p and p not in seen and os.path.isdir(p):
            seen.add(p)
            out.append(p)
    return out


_POSTER_NAMES = ("poster.jpg", "poster.jpeg", "poster.png", "poster.webp",
                 "folder.jpg", "folder.jpeg", "folder.png",
                 "cover.jpg", "cover.jpeg", "cover.png", "cover.webp")


def _local_art_url(folder: str, files: list[str]) -> str | None:
    """Serve an on-disk poster/cover (poster.jpg / folder.jpg / cover.jpg) via the
    backend poster endpoint, so thumbnails work from the scan with no API call."""
    low = {f.lower(): f for f in files}
    for cand in _POSTER_NAMES:
        if cand in low:
            return "/api/library/poster?path=" + quote(os.path.join(folder, low[cand]))
    return None


def _apply_meta_file(row, folder: str, kind: str) -> bool:
    """Meta-file-first import: if a managarr.json / NFO / OPF is present, populate the
    row's canonical fields + meta.providers from it (no API call). Returns True if a
    meta file was found, so the caller can skip API enrichment for this item."""
    data = sidecar.read_metadata(folder, kind)
    if not data:
        return False
    if data.get("title"):
        row.title = data["title"]
    if data.get("year") is not None and hasattr(row, "year"):
        row.year = data["year"]
    if data.get("overview") and hasattr(row, "overview"):
        row.overview = sidecar._sanitize_html(data["overview"])
    meta = dict(row.meta or {})
    provs = dict(meta.get("providers") or {})
    provs.update(data.get("providers") or {})
    if provs:
        meta["providers"] = provs
    meta["meta_source"] = data.get("_source")
    row.meta = meta
    if kind == "audiobook":
        if data.get("author"):
            row.author_name = data["author"]
        if data.get("asin"):
            row.asin = data["asin"]
        if data.get("isbn"):
            row.isbn = data["isbn"]
        if data.get("narrator"):
            row.narrator = data["narrator"]
        if data.get("subtitle"):
            row.subtitle = data["subtitle"]
        if data.get("publish_year"):
            row.publish_year = data["publish_year"]
        if data.get("series"):
            meta["scanned_series"] = data["series"]
        for k in ("genres", "publisher", "language"):
            if data.get(k):
                meta[k] = data[k]
        row.meta = meta
    return True


def _video_codec(path: str) -> str | None:
    if not settings.SCAN_PROBE_CODEC:
        return None
    try:
        out = subprocess.run(
            [settings.FFPROBE_BIN, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=20,
        )
        codec = out.stdout.strip().splitlines()
        return codec[0] if codec else None
    except Exception as exc:
        log.debug("ffprobe failed for %s: %s", path, exc)
        return None


def _largest_video(folder: str, files: list[str]) -> tuple[int, str | None]:
    total, largest, largest_size = 0, None, -1
    for f in files:
        if os.path.splitext(f)[1].lower() not in VIDEO_EXTS:
            continue
        try:
            sz = os.path.getsize(os.path.join(folder, f))
        except OSError:
            continue
        total += sz
        if sz > largest_size:
            largest_size, largest = sz, os.path.join(folder, f)
    return total, largest


# ── movies ────────────────────────────────────────────────────────────────────
def _movie_issues(files: list[str]) -> list[str]:
    issues = []
    if len([f for f in files if os.path.splitext(f)[1].lower() in VIDEO_EXTS]) > 1:
        issues.append("multiple_video_files")
    if not any(f.lower().endswith(".nfo") for f in files):
        issues.append("missing_nfo")
    if not any(re.match(r"poster\.", f, re.I) for f in files):
        issues.append("missing_poster")
    return issues


def scan_movies(session: Session) -> dict:
    roots = _roots(session, "movie")
    candidates: list[str] = []
    for root in roots:
        try:
            candidates += [os.path.join(root, n) for n in sorted(os.listdir(root))
                           if os.path.isdir(os.path.join(root, n))]
        except OSError as exc:
            log.warning("cannot list movie root %s: %s", root, exc)
    _set_progress("movie", running=True, total=len(candidates), processed=0, found=0,
                  started_at=datetime.utcnow().isoformat(), error=None)

    existing = {m.folder_path: m for m in session.exec(select(Movie)).all()}
    seen: set[str] = set()
    found = processed = 0

    for folder in candidates:
        processed += 1
        try:
            files = os.listdir(folder)
        except OSError:
            _set_progress("movie", processed=processed)
            continue
        videos = [f for f in files if os.path.splitext(f)[1].lower() in VIDEO_EXTS]
        if videos:
            seen.add(folder)
            name = os.path.basename(folder)
            m = _YEAR_RE.match(name)
            title = m.group(1).strip() if m else name
            year = int(m.group(2)) if m else None
            total_sz, largest = _largest_video(folder, files)
            row = existing.get(folder)
            if row:
                if not (row.meta or {}).get("providers", {}).get("tmdb"):
                    row.title = title
                row.year = year
                row.file_size_bytes = total_sz or None
                row.video_codec = _video_codec(largest) if largest else row.video_codec
                row.issues = _movie_issues(files)
                row.has_file = True
                row.last_scanned = datetime.utcnow()
            else:
                row = Movie(title=title, year=year, folder_path=folder,
                            file_size_bytes=total_sz or None,
                            video_codec=_video_codec(largest) if largest else None,
                            issues=_movie_issues(files), has_file=True)
            _apply_meta_file(row, folder, "movie")
            if not (row.poster_url or "").startswith("http"):
                art = _local_art_url(folder, files)
                if art:
                    row.poster_url = art
            session.add(row)
            found += 1
        if processed % settings.SCAN_BATCH == 0:
            session.commit()
            _set_progress("movie", processed=processed, found=found)

    for folder, row in existing.items():
        if folder not in seen:
            session.delete(row)
    session.commit()
    _set_progress("movie", running=False, processed=processed, found=found,
                  finished_at=datetime.utcnow().isoformat())
    return {"kind": "movie", "found": found, "total": len(candidates)}


# ── tv ────────────────────────────────────────────────────────────────────────
def scan_tv(session: Session) -> dict:
    roots = _roots(session, "tv")
    candidates: list[str] = []
    for root in roots:
        try:
            candidates += [os.path.join(root, n) for n in sorted(os.listdir(root))
                           if os.path.isdir(os.path.join(root, n))]
        except OSError as exc:
            log.warning("cannot list tv root %s: %s", root, exc)
    _set_progress("tv", running=True, total=len(candidates), processed=0, found=0,
                  started_at=datetime.utcnow().isoformat(), error=None)

    existing = {s.folder_path: s for s in session.exec(select(Series)).all()}
    seen: set[str] = set()
    found = processed = 0

    for show_path in candidates:
        processed += 1
        seen.add(show_path)
        try:
            show_files = os.listdir(show_path)
        except OSError:
            _set_progress("tv", processed=processed)
            continue

        name = os.path.basename(show_path)
        m = _YEAR_RE.match(name)
        title = m.group(1).strip() if m else name
        year = int(m.group(2)) if m else None
        issues = [] if any(f.lower() == "tvshow.nfo" for f in show_files) else ["missing_nfo"]

        series = existing.get(show_path)
        if series:
            series.year = year
            series.issues = issues
            series.last_scanned = datetime.utcnow()
        else:
            series = Series(title=title, year=year, folder_path=show_path, issues=issues)
        _apply_meta_file(series, show_path, "tv")
        if not (series.poster_url or "").startswith("http"):
            art = _local_art_url(show_path, show_files)
            if art:
                series.poster_url = art
        session.add(series)
        session.flush()

        eps = {(e.season, e.episode): e for e in
               session.exec(select(Episode).where(Episode.series_id == series.id)).all()}
        seen_eps: set[tuple[int, int]] = set()
        for season_dir in sorted(show_files):
            season_path = os.path.join(show_path, season_dir)
            if not os.path.isdir(season_path):
                continue
            sm = _SEASON_RE.search(season_dir)
            if not sm:
                continue
            season_num = int(sm.group(1) or sm.group(2))
            # Recurse: some releases nest the episodes one level deeper inside the
            # season dir (e.g. "Season 04/Peaky Blinders S04 1080p.../S04E01.mkv"),
            # which a flat listdir misses -> the whole season reads as has_file=False
            # and gets perpetually re-grabbed. Walk the season subtree instead.
            try:
                ep_paths = sorted(
                    os.path.join(dp, fn)
                    for dp, _dn, fns in os.walk(season_path) for fn in fns)
            except OSError:
                continue
            for ep_path in ep_paths:
                ep_file = os.path.basename(ep_path)
                em = _EP_RE.search(ep_file)
                if not em or os.path.splitext(ep_file)[1].lower() not in VIDEO_EXTS:
                    continue
                ep_num = int(em.group(2))
                key = (season_num, ep_num)
                if key in seen_eps:
                    continue
                seen_eps.add(key)
                ep = eps.get(key)
                if ep:
                    ep.file_path = ep_path
                    ep.has_file = True
                else:
                    ep = Episode(series_id=series.id, season=season_num, episode=ep_num,
                                 file_path=ep_path, has_file=True)
                session.add(ep)
        for key, ep in eps.items():
            if key not in seen_eps and ep.has_file:
                session.delete(ep)
        found += 1
        if processed % settings.SCAN_BATCH == 0:
            session.commit()
            _set_progress("tv", processed=processed, found=found)

    for folder, series in existing.items():
        if folder not in seen:
            for ep in session.exec(select(Episode).where(Episode.series_id == series.id)).all():
                session.delete(ep)
            session.delete(series)
    session.commit()
    _set_progress("tv", running=False, processed=processed, found=found,
                  finished_at=datetime.utcnow().isoformat())
    return {"kind": "tv", "found": found, "total": len(candidates)}


# ── audiobooks ────────────────────────────────────────────────────────────────
def scan_audiobooks(session: Session) -> dict:
    roots = _roots(session, "audiobook")
    candidates: list[str] = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            # Honour Audiobookshelf's `.ignore` marker: a folder holding one (seed copies,
            # quarantined duplicates) is skipped along with everything beneath it, so the
            # catalog lists the same books ABS does.
            if IGNORE_MARKER in filenames:
                dirnames[:] = []
                continue
            if any(os.path.splitext(f)[1].lower() in AUDIO_EXTS for f in filenames):
                candidates.append(dirpath)
    _set_progress("audiobook", running=True, total=len(candidates), processed=0, found=0,
                  started_at=datetime.utcnow().isoformat(), error=None)

    existing = {b.folder_path: b for b in session.exec(select(Audiobook)).all()}
    seen: set[str] = set()
    found = processed = 0

    for dirpath in candidates:
        processed += 1
        seen.add(dirpath)
        try:
            all_files = os.listdir(dirpath)
        except OSError:
            _set_progress("audiobook", processed=processed)
            continue
        audio = [f for f in all_files if os.path.splitext(f)[1].lower() in AUDIO_EXTS]
        root = next((r for r in roots if dirpath.startswith(r)), roots[0] if roots else dirpath)
        parts = [p for p in os.path.relpath(dirpath, root).split(os.sep) if p and p != "."]
        title = parts[-1] if parts else os.path.basename(dirpath)
        author = parts[0] if parts else None
        series_name = parts[1] if len(parts) >= 3 else None
        has_audio = bool(audio)
        has_m4b = any(f.lower().endswith(".m4b") for f in audio)

        row = existing.get(dirpath)
        if row:
            row.has_audio = has_audio
            row.has_m4b = has_m4b
            row.issues = [] if has_m4b else ["no_m4b"]
            row.last_scanned = datetime.utcnow()
            if not row.author_name and author:
                row.author_name = author
        else:
            row = Audiobook(title=title, author_name=author, folder_path=dirpath,
                            has_audio=has_audio, has_m4b=has_m4b,
                            issues=[] if has_m4b else ["no_m4b"],
                            meta={"scanned_series": series_name} if series_name else None)
        _apply_meta_file(row, dirpath, "audiobook")
        if not (row.cover_url or "").startswith("http"):
            art = _local_art_url(dirpath, all_files)
            if art:
                row.cover_url = art
        session.add(row)
        found += 1
        if processed % settings.SCAN_BATCH == 0:
            session.commit()
            _set_progress("audiobook", processed=processed, found=found)

    for folder, row in existing.items():
        if folder not in seen:
            # clear FK-referencing m4b conversion jobs, else the delete violates the constraint
            for job in session.exec(select(M4bJob).where(M4bJob.audiobook_id == row.id)).all():
                session.delete(job)
            session.delete(row)
    session.commit()
    _set_progress("audiobook", running=False, processed=processed, found=found,
                  finished_at=datetime.utcnow().isoformat())
    return {"kind": "audiobook", "found": found, "total": len(candidates)}


def scan_all(session: Session) -> dict:
    return {"movies": scan_movies(session), "tv": scan_tv(session),
            "audiobooks": scan_audiobooks(session)}
