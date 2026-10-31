"""HEVC transcode service — CPU-only libx265, manual approval, NFS-safe.

Targets an NVIDIA Shield's direct-play sweet spot: the Shield direct-plays HEVC
(incl. Main10 / 4K), so we transcode legacy/oversized video TO ``hevc`` and never
to av1. Workflow:

  scan_candidates -> TranscodeJob(status="candidate")
  approve(id)     -> status="approved"
  process_queue   -> runs approved jobs (respecting concurrency + time window +
                     "is someone streaming?") via run_job.

NFS-safe: ffmpeg writes a temp file in the SAME directory as the source, verifies
the output (ffprobe duration within tolerance, smaller size), then an atomic
``os.replace`` over the ORIGINAL library file. We NEVER touch the qBit /downloads
copy (that hardlink/copy is independent — only the library file is replaced).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from ..config import settings
from ..db import session_scope
from ..http import client
from ..models import Episode, Movie, TranscodeJob
from ..notify import notify

log = logging.getLogger("managarr.transcode")

# Module-level concurrency guard (CONTRACT: settings.TRANSCODE_MAX_CONCURRENT).
TRANSCODE_SEMAPHORE = threading.Semaphore(max(1, settings.TRANSCODE_MAX_CONCURRENT))

# codecs that don't direct-play efficiently on a Shield -> transcode to hevc
_TARGET_CODECS = {"h264", "mpeg2video", "mpeg2", "mpeg4", "vc1", "msmpeg4v3", "wmv3"}
# even hevc gets flagged if the bitrate is absurd (e.g. remux)
_HEVC_BITRATE_CEILING = 25_000_000  # ~25 Mbps
_LARGE_FILE_BYTES = 15 * 1024**3    # 15 GiB
_DURATION_TOLERANCE = 2.0           # seconds


# ── ffprobe helpers ───────────────────────────────────────────────────────────
def _ffprobe_json(path: str) -> dict:
    try:
        out = subprocess.run(
            [settings.FFPROBE_BIN, "-v", "error", "-show_entries",
             "format=duration,bit_rate,size:stream=codec_name,codec_type",
             "-of", "json", path],
            capture_output=True, text=True, timeout=60,
        )
        import json
        return json.loads(out.stdout or "{}")
    except Exception as exc:
        log.warning("ffprobe failed for %s: %s", path, exc)
        return {}


def _probe_facts(path: str) -> dict:
    data = _ffprobe_json(path)
    fmt = data.get("format") or {}
    vcodec = None
    for st in data.get("streams") or []:
        if st.get("codec_type") == "video":
            vcodec = st.get("codec_name")
            break
    dur = None
    try:
        dur = float(fmt.get("duration")) if fmt.get("duration") else None
    except (TypeError, ValueError):
        pass
    br = None
    try:
        br = int(fmt.get("bit_rate")) if fmt.get("bit_rate") else None
    except (TypeError, ValueError):
        pass
    return {"codec": vcodec, "duration": dur, "bit_rate": br}


def _flag_reason(path: str) -> Optional[str]:
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    facts = _probe_facts(path)
    codec = (facts.get("codec") or "").lower()
    br = facts.get("bit_rate")
    if codec in _TARGET_CODECS:
        return f"{codec} -> hevc (legacy codec, {size // 1024**2} MiB)"
    if codec in ("hevc", "h265"):
        if br and br > _HEVC_BITRATE_CEILING:
            return f"hevc but {br // 1_000_000} Mbps (> ceiling) — re-encode smaller"
        if size > _LARGE_FILE_BYTES:
            return f"hevc but {size // 1024**3} GiB (oversized) — re-encode smaller"
        return None  # already efficient hevc
    if codec and size > _LARGE_FILE_BYTES:
        return f"{codec}, {size // 1024**3} GiB oversized -> hevc"
    return None


# ── candidate scan ────────────────────────────────────────────────────────────
def _existing_paths(session: Session) -> set[str]:
    return {j.source_path for j in session.exec(select(TranscodeJob)).all()}


def scan_candidates(session: Session) -> dict:
    """ffprobe library video files; create TranscodeJob(status=candidate) for the
    ones that wouldn't direct-play efficiently on a Shield."""
    existing = _existing_paths(session)
    created = 0

    # movies: each Movie's largest video file (folder may have one)
    for m in session.exec(select(Movie).where(Movie.has_file == True)).all():  # noqa: E712
        for vid in _video_files(m.folder_path):
            if vid in existing:
                continue
            reason = _flag_reason(vid)
            if reason:
                try:
                    size = os.path.getsize(vid)
                except OSError:
                    size = None
                session.add(TranscodeJob(media_kind="movie", item_id=m.id, source_path=vid,
                                         status="candidate", reason=reason,
                                         source_size_bytes=size))
                existing.add(vid)
                created += 1

    # tv: each episode file
    for ep in session.exec(select(Episode).where(Episode.has_file == True)).all():  # noqa: E712
        if not ep.file_path or ep.file_path in existing:
            continue
        reason = _flag_reason(ep.file_path)
        if reason:
            try:
                size = os.path.getsize(ep.file_path)
            except OSError:
                size = None
            session.add(TranscodeJob(media_kind="tv", item_id=ep.series_id,
                                     source_path=ep.file_path, status="candidate",
                                     reason=reason, source_size_bytes=size))
            existing.add(ep.file_path)
            created += 1

    session.commit()
    return {"candidates_created": created}


def _video_files(folder: str) -> list[str]:
    from ..pipeline.scanner import VIDEO_EXTS
    out = []
    if not folder or not os.path.isdir(folder):
        return out
    try:
        for f in os.listdir(folder):
            if os.path.splitext(f)[1].lower() in VIDEO_EXTS:
                out.append(os.path.join(folder, f))
    except OSError:
        pass
    return out


# ── approve / skip ────────────────────────────────────────────────────────────
def approve(job_id: int) -> dict:
    with session_scope() as s:
        job = s.get(TranscodeJob, job_id)
        if not job:
            raise ValueError(f"transcode job {job_id} not found")
        job.status = "approved"
        s.add(job)
        return {"id": job.id, "status": job.status}


def skip(job_id: int) -> dict:
    with session_scope() as s:
        job = s.get(TranscodeJob, job_id)
        if not job:
            raise ValueError(f"transcode job {job_id} not found")
        job.status = "skipped"
        s.add(job)
        return {"id": job.id, "status": job.status}


# ── time window ───────────────────────────────────────────────────────────────
def _within_window() -> bool:
    win = (settings.TRANSCODE_WINDOW or "").strip()
    if not win or "-" not in win:
        return True
    try:
        start_s, end_s = win.split("-", 1)
        sh, sm = (int(x) for x in start_s.split(":"))
        eh, em = (int(x) for x in end_s.split(":"))
    except Exception:
        return True
    now = datetime.now().time()
    start_min = sh * 60 + sm
    end_min = eh * 60 + em
    cur = now.hour * 60 + now.minute
    if start_min <= end_min:
        return start_min <= cur <= end_min
    # window wraps past midnight (e.g. 23:00-06:00)
    return cur >= start_min or cur <= end_min


def pause_if_streaming() -> bool:
    """Best-effort: True if Plex has an active *video* session (so we should defer)."""
    if not settings.PLEX_URL or not settings.PLEX_TOKEN:
        return False
    try:
        with client(timeout=10) as c:
            r = c.get(f"{settings.PLEX_URL.rstrip('/')}/status/sessions",
                      headers={"X-Plex-Token": settings.PLEX_TOKEN,
                               "Accept": "application/json"})
            r.raise_for_status()
            data = r.json() or {}
    except Exception as exc:
        log.debug("plex sessions check failed: %s", exc)
        return False
    container = data.get("MediaContainer") or {}
    for md in container.get("Metadata") or []:
        if md.get("type") in ("movie", "episode", "clip") or (md.get("Media")):
            return True
    return False


# ── run one job ───────────────────────────────────────────────────────────────
def run_job(job_id: int) -> dict:
    """Transcode one job to HEVC and atomically replace the library file.
    Honors the concurrency semaphore. Returns a result summary."""
    acquired = TRANSCODE_SEMAPHORE.acquire(blocking=False)
    if not acquired:
        return {"id": job_id, "status": "deferred", "reason": "at max concurrency"}
    try:
        return _run_job_locked(job_id)
    finally:
        TRANSCODE_SEMAPHORE.release()


def _run_job_locked(job_id: int) -> dict:
    with session_scope() as s:
        job = s.get(TranscodeJob, job_id)
        if not job:
            raise ValueError(f"transcode job {job_id} not found")
        src = job.source_path
        media_kind = job.media_kind
        job.status = "running"
        s.add(job)

    if not os.path.isfile(src):
        return _finish(job_id, "error", message="source file missing")

    try:
        src_size = os.path.getsize(src)
        src_facts = _probe_facts(src)
        src_dur = src_facts.get("duration")
    except OSError as exc:
        return _finish(job_id, "error", message=f"stat failed: {exc}")

    # temp output in the SAME dir (NFS-safe atomic replace)
    base, _ext = os.path.splitext(src)
    tmp = f"{base}.managarr-hevc-{uuid.uuid4().hex[:8]}.mkv"
    cmd = [
        settings.FFMPEG_BIN, "-y", "-i", src,
        "-c:v", "libx265",
        "-crf", str(settings.TRANSCODE_CRF),
        "-preset", settings.TRANSCODE_PRESET,
        "-x265-params", f"pools={settings.TRANSCODE_THREADS}",
        "-threads", str(settings.TRANSCODE_THREADS),
        "-c:a", "copy", "-c:s", "copy",
        tmp,
    ]
    log.info("transcoding %s -> %s", src, tmp)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60 * 60 * 12)
    except Exception as exc:
        _cleanup(tmp)
        return _finish(job_id, "error", message=f"ffmpeg failed to run: {exc}")

    if proc.returncode != 0 or not os.path.isfile(tmp):
        tail = (proc.stderr or "")[-500:]
        _cleanup(tmp)
        return _finish(job_id, "error", message=f"ffmpeg rc={proc.returncode}: {tail}")

    # verify output
    out_facts = _probe_facts(tmp)
    out_dur = out_facts.get("duration")
    try:
        out_size = os.path.getsize(tmp)
    except OSError:
        out_size = None

    if src_dur and out_dur and abs(src_dur - out_dur) > _DURATION_TOLERANCE:
        _cleanup(tmp)
        return _finish(job_id, "error",
                       message=f"duration mismatch src={src_dur:.1f}s out={out_dur:.1f}s")
    if out_size is None or (src_size and out_size >= src_size):
        _cleanup(tmp)
        return _finish(job_id, "error",
                       message=f"output not smaller (src={src_size} out={out_size}); kept original")

    # atomic replace over the ORIGINAL library file (never the /downloads copy)
    final = base + ".mkv"
    try:
        os.replace(tmp, final)
        # if the source had a different extension/name, remove the stale original
        if os.path.abspath(final) != os.path.abspath(src) and os.path.isfile(src):
            try:
                os.unlink(src)
            except OSError:
                pass
    except OSError as exc:
        _cleanup(tmp)
        return _finish(job_id, "error", message=f"atomic replace failed: {exc}")

    notify(f"Transcoded: {os.path.basename(final)}",
           f"{src_size // 1024**2} MiB -> {out_size // 1024**2} MiB (hevc)",
           kind="transcoded", media_kind=media_kind)
    return _finish(job_id, "done", out_size=out_size, final_path=final)


def _finish(job_id: int, status: str, *, message: str = "", out_size: Optional[int] = None,
            final_path: Optional[str] = None) -> dict:
    with session_scope() as s:
        job = s.get(TranscodeJob, job_id)
        if job:
            job.status = status
            job.message = message or job.message
            if out_size is not None:
                job.output_size_bytes = out_size
            if final_path:
                job.source_path = final_path
            job.finished_at = datetime.utcnow()
            s.add(job)
    if status == "error":
        log.warning("transcode job %s error: %s", job_id, message)
    return {"id": job_id, "status": status, "message": message, "output_size_bytes": out_size}


def _cleanup(path: str) -> None:
    try:
        if path and os.path.isfile(path):
            os.unlink(path)
    except OSError:
        pass


# ── queue processor ───────────────────────────────────────────────────────────
def process_queue(session: Session) -> dict:
    """Run approved jobs within concurrency / window / streaming limits."""
    if not _within_window():
        return {"status": "outside window", "ran": 0}
    if pause_if_streaming():
        return {"status": "deferred — active Plex stream", "ran": 0}

    approved = session.exec(
        select(TranscodeJob).where(TranscodeJob.status == "approved")
    ).all()
    ran = 0
    results = []
    for job in approved:
        # acquire (non-blocking) inside run_job; stop once we hit max concurrency
        res = run_job(job.id)
        results.append(res)
        if res.get("status") == "deferred":
            break
        if res.get("status") in ("done", "error"):
            ran += 1
        # re-check streaming between jobs
        if pause_if_streaming():
            break
    return {"status": "ok", "ran": ran, "results": results}
