"""Import completed downloads into the library.

For each qBit torrent in the ``managarr`` category that is complete and matches an
open Grab, hardlink its files into the Grab's ``save_path`` library folder
(fallback copy across devices; cross-PVC safe via stage+os.replace). The download
itself is NEVER moved or deleted — qBit keeps seeding from /downloads.

After files land we write the metadata sidecar and notify.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import uuid

from sqlmodel import Session, select

from ..config import settings
from ..db import session_scope
from ..metadata.sidecar import build_metadata_dict, write_sidecar
from ..models import Audiobook, Grab, Movie, Series
from ..notify import notify
from ..services import plex_sync
from .qbit import QBitClient

log = logging.getLogger("managarr.importer")

# qBit "completed-ish" states.
_DONE_STATES = {"uploading", "stalledUP", "pausedUP", "queuedUP", "forcedUP", "checkingUP"}


# ── scene-archive handling ────────────────────────────────────────────────────
# Many scene releases ship the video sealed inside a multi-part RAR/7z/zip set
# (e.g. name.rar + name.r00..r90, or name.part01.rar, or name.7z.001) with NO
# loose video file. Hardlinking those parts imports nothing playable, so a normal
# grab never appears and an *upgrade* aborts with "no new video file" — leaving the
# item stuck "upgrading". We detect such sets and extract the real video with
# `unar` (shipped in the image) so a genuine file lands in the library.
_ARCHIVE_PART_RE = re.compile(
    r"(\.rar|\.r\d{2}|\.\d{3}|\.7z|\.7z\.\d+|\.zip|\.z\d{2}|\.sfv)$", re.IGNORECASE)
_PARTNN_RAR_RE = re.compile(r"\.part(\d+)\.rar$", re.IGNORECASE)


def _is_archive_part(name: str) -> bool:
    """True for a file that is one volume of a (possibly multi-part) archive set —
    these are never linked into the library on their own (the video is extracted)."""
    return bool(_ARCHIVE_PART_RE.search(name) or _PARTNN_RAR_RE.search(name))


def _find_main_archive(folder: str) -> str | None:
    """Return the first volume of an archive set in ``folder`` to hand to unar
    (which follows the remaining volumes automatically), or None if there is none."""
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    # new-style multi-part: name.partNN.rar — hand unar the lowest NN
    partrars = [(int(m.group(1)), n) for n in names if (m := _PARTNN_RAR_RE.search(n))]
    if partrars:
        return os.path.join(folder, min(partrars)[1])
    # old-style: name.rar (+ name.r00, r01 …) — the .rar is the first volume
    rars = sorted(n for n in names
                  if n.lower().endswith(".rar") and not _PARTNN_RAR_RE.search(n))
    if rars:
        return os.path.join(folder, rars[0])
    # split 7z / generic split: first volume ends .001 (or .7z.001)
    firsts = sorted(n for n in names if re.search(r"\.(7z\.)?0*1$", n, re.IGNORECASE))
    if firsts:
        return os.path.join(folder, firsts[0])
    # single-volume 7z / zip
    solo = sorted(n for n in names if n.lower().endswith((".7z", ".zip")))
    if solo:
        return os.path.join(folder, solo[0])
    return None


def _extract_archives(src_dir: str, dest_dir: str) -> int:
    """Extract the archive set in ``src_dir`` flat into ``dest_dir``. Returns the
    number of NEW video files produced. Degrades to 0 (never raises) if there is no
    archive or the extractor is missing/fails, so import stays best-effort."""
    main = _find_main_archive(src_dir)
    if not main:
        return 0
    from .scanner import VIDEO_EXTS
    os.makedirs(dest_dir, exist_ok=True)
    before = {n for n in os.listdir(dest_dir) if os.path.splitext(n)[1].lower() in VIDEO_EXTS}
    try:
        subprocess.run(
            ["unar", "-quiet", "-force-overwrite", "-no-directory",
             "-output-directory", dest_dir, main],
            check=True, timeout=7200,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("archive extraction failed for %s: %s", main, exc)
        return 0
    after = {n for n in os.listdir(dest_dir) if os.path.splitext(n)[1].lower() in VIDEO_EXTS}
    return len(after - before)


def _hardlink_or_copy(src: str, dst: str) -> str:
    """Hardlink src->dst (preferred, preserves seeding); fall back to a staged copy
    across devices. Returns the method used."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        return "exists"
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:
        # cross-device (EXDEV) or other: stage to a temp then atomic replace
        tmp = dst + f".part-{uuid.uuid4().hex[:8]}"
        try:
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
            return "copy"
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def _import_grab_files(grab: Grab, content_path: str) -> int:
    """Link every file under content_path into grab.save_path. Returns count."""
    linked = 0
    if not os.path.exists(content_path):
        log.warning("content path missing for grab %s: %s", grab.id, content_path)
        return 0
    os.makedirs(grab.save_path, exist_ok=True)

    if os.path.isfile(content_path):
        dst = os.path.join(grab.save_path, os.path.basename(content_path))
        if _hardlink_or_copy(content_path, dst) != "exists":
            linked += 1
        return linked

    from .scanner import VIDEO_EXTS
    downloaded_video = False       # did the release ship a loose (non-archived) video?
    archive_dir: str | None = None  # where its archive volumes live, if any
    for dirpath, _dirs, files in os.walk(content_path):
        rel = os.path.relpath(dirpath, content_path)
        for f in files:
            if f.endswith(".torrent") or f.endswith(".part"):
                continue
            # Archive volumes are never linked into the library on their own — the
            # real video is extracted from them below.
            if _is_archive_part(f):
                if archive_dir is None:
                    archive_dir = dirpath
                continue
            if os.path.splitext(f)[1].lower() in VIDEO_EXTS:
                downloaded_video = True
            src = os.path.join(dirpath, f)
            dst = os.path.join(grab.save_path, os.path.basename(f) if rel == "." else os.path.join(rel, f))
            try:
                if _hardlink_or_copy(src, dst) != "exists":
                    linked += 1
            except OSError as exc:
                log.warning("failed to link %s -> %s: %s", src, dst, exc)

    # The release is RAR/7z/zip-packed with no loose video — extract the real video
    # into the library so the grab (and any upgrade finalize) sees a new file. Keyed
    # on what the DOWNLOAD contained, not the destination: on an upgrade the old file
    # is still present in save_path, so a destination check would wrongly skip this.
    if not downloaded_video and archive_dir:
        extracted = _extract_archives(archive_dir, grab.save_path)
        if extracted:
            log.info("grab %s: extracted %d video file(s) from archive set", grab.id, extracted)
            linked += extracted
    return linked


def _mark_item_present(grab: Grab, session: Session) -> None:
    """Flag the library item as present the moment its download is imported.

    Movies stage in /new-movies (not a scanned root) until watched+moved, so the
    scanner never sees them there — without this they'd stay has_file=False and the
    RSS job would re-grab an already-downloaded movie. The importer KNOWS the file
    just landed, so it's the authoritative place to set presence."""
    if not grab.item_id:
        return
    if grab.media_kind == "movie":
        m = session.get(Movie, grab.item_id)
        if m and not m.has_file:
            m.has_file = True
            session.add(m)
    elif grab.media_kind == "audiobook":
        b = session.get(Audiobook, grab.item_id)
        if b and not b.has_audio:
            b.has_audio = True
            session.add(b)


def _sidecar_for_grab(grab: Grab, session: Session) -> None:
    model = {"movie": Movie, "tv": Series, "series": Series, "audiobook": Audiobook}.get(grab.media_kind)
    item = session.get(model, grab.item_id) if (model and grab.item_id) else None
    if not item:
        return
    kind = "tv" if grab.media_kind == "series" else grab.media_kind
    try:
        write_sidecar(grab.save_path, build_metadata_dict(item, kind))
    except Exception:
        log.exception("sidecar write failed for grab %s", grab.id)


def _enrich_after_import(grab: Grab, session: Session) -> None:
    """Shells born from watchlist/search carry provider ids but no artwork — pull
    full metadata at import so the new entry shows matched immediately. refresh
    also rewrites the sidecar with the fetched art/overview."""
    from ..metadata.aggregator import _is_enriched, refresh_metadata
    model = {"movie": Movie, "tv": Series, "series": Series, "audiobook": Audiobook}.get(grab.media_kind)
    item = session.get(model, grab.item_id) if (model and grab.item_id) else None
    if not item or _is_enriched(item):
        return
    kind = "tv" if grab.media_kind == "series" else grab.media_kind
    try:
        refresh_metadata(item.id, kind, session)
    except Exception:
        log.exception("post-import enrich failed for grab %s", grab.id)


def _norm_release(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _savepath_match_ok(grab: Grab, torrent: dict, torrents: list[dict]) -> bool:
    """Guard for the save-path fallback match. Two torrents can share one folder —
    an upgrade downloads next to the file it replaces — and matching the wrong one
    once marked a 0% upgrade grab 'imported' off the OLD torrent's completion,
    which then deleted the only copy. The torrent's name must match the grab's
    release title, unless it's the only torrent in that folder."""
    tn = _norm_release(torrent.get("name"))
    gt = _norm_release(grab.title)
    if tn and gt and (gt in tn or tn in gt):
        return True
    sp = torrent.get("save_path")
    return sum(1 for x in torrents if x.get("save_path") == sp) == 1


def import_completed(session: Session) -> dict:
    qbit = QBitClient()
    torrents = qbit.torrents(category=settings.QBT_CATEGORY)
    if not torrents:
        return {"imported": 0, "checked": 0}

    grabs = session.exec(
        select(Grab).where(Grab.category == settings.QBT_CATEGORY,
                           Grab.state.in_(["grabbed", "downloading", "importing"]))
    ).all()
    by_hash = {(g.infohash or "").lower(): g for g in grabs if g.infohash}
    by_savepath = {g.save_path: g for g in grabs}

    imported = 0
    checked = 0
    abs_audiobook_imported = False
    tv_imported = False
    plex_folders: set[str] = set()  # show/movie folders to point Plex at after import
    for t in torrents:
        progress = t.get("progress", 0)
        state = t.get("state", "")
        complete = progress >= 1.0 or state in _DONE_STATES
        if not complete:
            continue
        checked += 1

        h = (t.get("hash") or "").lower()
        grab = by_hash.get(h)
        if not grab:
            cand = by_savepath.get(t.get("save_path", ""))
            if cand and _savepath_match_ok(cand, t, torrents):
                grab = cand
        if not grab or grab.state == "imported":
            continue

        content_path = t.get("content_path") or t.get("save_path") or grab.save_path
        grab.state = "importing"
        session.add(grab)
        session.commit()

        try:
            n = _import_grab_files(grab, content_path)
            _sidecar_for_grab(grab, session)
            _mark_item_present(grab, session)
            _enrich_after_import(grab, session)
            grab.state = "imported"
            grab.message = f"imported {n} file(s)"
            session.add(grab)
            session.commit()
            # An upgrade grab: now that the better release has landed, remove the old
            # file(s) it replaces (no-op for ordinary grabs).
            if grab.source == "upgrade" and grab.media_kind == "movie":
                from .upgrade import finalize_movie_upgrade
                finalize_movie_upgrade(grab, session)
            imported += 1
            if grab.media_kind == "audiobook":
                abs_audiobook_imported = True
            if grab.media_kind in ("tv", "series"):
                tv_imported = True
                plex_folders.add(_show_folder(grab.save_path))
            elif grab.media_kind == "movie":
                plex_folders.add(grab.save_path)
            notify(f"Imported: {grab.title}", f"{n} file(s) -> {grab.save_path}",
                   kind="imported", media_kind=grab.media_kind, meta={"item_id": grab.item_id})
        except Exception as exc:
            log.exception("import failed for grab %s", grab.id)
            grab.state = "failed"
            grab.message = f"import error: {exc}"
            session.add(grab)
            session.commit()
            notify(f"Import failed: {grab.title}", str(exc), kind="failed",
                   media_kind=grab.media_kind, priority="high")

    # A new audiobook landed in the shared library folder — poke Audiobookshelf to
    # rescan so it appears there without waiting for ABS's own scan (best-effort).
    if abs_audiobook_imported:
        try:
            from ..services import abs as abs_svc
            abs_svc.trigger_scan()
        except Exception:  # noqa: BLE001
            log.warning("ABS scan trigger failed", exc_info=True)

    # A TV season-pack import lands multiple episode files, but the importer can't
    # cheaply map pack -> individual episodes, so _mark_item_present is a no-op for
    # tv. Without reconciling episode.has_file the missing-detector re-queues the
    # same season forever (re-grabbing a torrent already seeding in the library).
    # Rescan the TV library from disk so the just-landed episodes flip to has_file.
    if tv_imported:
        try:
            from . import scanner
            with session_scope() as s2:
                scanner.scan_tv(s2)
        except Exception:  # noqa: BLE001
            log.warning("post-import TV rescan failed", exc_info=True)

    # Plex never sees files written over NFS by this pod (no inotify), and its
    # periodic scan is off — so a new show/season or movie would sit invisible in Plex
    # until someone scans by hand. Point Plex at each show/movie folder that just changed.
    for folder in sorted(plex_folders):
        plex_sync.partial_scan(folder)

    return {"imported": imported, "checked": checked}


def _show_folder(save_path: str) -> str:
    """``/tv/<Show>/Season 01`` -> ``/tv/<Show>`` (the folder Plex should rescan);
    anything not under TV_PATH is returned as-is."""
    root = settings.TV_PATH.rstrip("/")
    if save_path.startswith(root + "/"):
        top = os.path.relpath(save_path, root).split(os.sep)[0]
        return os.path.join(root, top)
    return save_path
