"""Audiobook MP3 -> single chapterized .m4b conversion.

Concatenates a multi-file MP3 (or other) audiobook into ONE ``.m4b`` (AAC) with one
chapter per source file, chapter bounds derived from each file's duration. Runs in a
background thread; progress + result live on the ``M4bJob`` row.

Non-destructive: the source files are KEPT (delete them yourself once you've verified
the m4b) — consistent with the seed-preservation rule. The new .m4b lands in the same
folder; the next scan (and this job) sets ``has_m4b=True``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from .. import settings_store
from ..config import settings
from ..db import session_scope
from ..models import Audiobook, M4bJob
from ..notify import notify

log = logging.getLogger("managarr.m4b")

# Concurrency gate: at most M4B_MAX_CONCURRENT conversions run at once (default 1 —
# CPU + NFS friendly). Queued jobs wait their turn. The limit is re-read each time a
# slot frees, so changing it in Settings takes effect without a restart.
_M4B_CV = threading.Condition()
_m4b_active = 0


def _m4b_limit() -> int:
    raw = settings_store.get_setting("M4B_MAX_CONCURRENT", None)
    try:
        return max(1, int(raw)) if raw not in (None, "") else max(1, settings.M4B_MAX_CONCURRENT)
    except (TypeError, ValueError):
        return max(1, settings.M4B_MAX_CONCURRENT)


def _acquire_slot() -> None:
    global _m4b_active
    with _M4B_CV:
        while _m4b_active >= _m4b_limit():
            _M4B_CV.wait()
        _m4b_active += 1


def _release_slot() -> None:
    global _m4b_active
    with _M4B_CV:
        _m4b_active = max(0, _m4b_active - 1)
        _M4B_CV.notify()
SRC_EXTS = (".mp3", ".m4a", ".ogg", ".flac", ".opus", ".aac", ".wav", ".wma")
_DUR_TOLERANCE = 8.0  # seconds


def _natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def source_files(folder: str) -> list[str]:
    """Audio source files in `folder`, natural-sorted (01, 02, … 10), excluding any
    existing .m4b (we're producing one)."""
    try:
        names = [f for f in os.listdir(folder)
                 if os.path.splitext(f)[1].lower() in SRC_EXTS
                 and not f.lower().endswith(".m4b")]
    except OSError:
        return []
    names.sort(key=_natural_key)
    return [os.path.join(folder, n) for n in names]


def _duration(path: str) -> Optional[float]:
    try:
        out = subprocess.run(
            [settings.FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
             "-of", "json", path], capture_output=True, text=True, timeout=120)
        d = (json.loads(out.stdout or "{}").get("format") or {}).get("duration")
        return float(d) if d else None
    except Exception as exc:
        log.warning("ffprobe duration failed for %s: %s", path, exc)
        return None


def _chapter_title(path: str, idx: int) -> str:
    name = os.path.splitext(os.path.basename(path))[0]
    cleaned = re.sub(r"^\s*\d+\s*[-_.)]+\s*", "", name).strip(" -_")
    return cleaned or f"Chapter {idx}"


def _ff_escape(p: str) -> str:
    return p.replace("'", "'\\''")


def can_convert(folder: str) -> bool:
    return len(source_files(folder)) >= 1


def start_convert(audiobook_id: int) -> dict:
    """Create an M4bJob and kick off the conversion in the background. Returns a
    plain dict snapshot (the ORM row would be detached once the session closes)."""
    with session_scope() as s:
        book = s.get(Audiobook, audiobook_id)
        if not book:
            raise ValueError(f"audiobook {audiobook_id} not found")
        files = source_files(book.folder_path)
        if not files:
            raise ValueError("no convertible audio files in this folder")
        size = sum((os.path.getsize(f) for f in files if os.path.isfile(f)), 0)
        job = M4bJob(audiobook_id=audiobook_id, folder_path=book.folder_path,
                     status="queued", num_files=len(files), source_size_bytes=size or None)
        s.add(job)
        s.commit()
        s.refresh(job)
        snapshot = {"id": job.id, "audiobook_id": job.audiobook_id, "status": job.status,
                    "progress": job.progress, "num_files": job.num_files,
                    "folder_path": job.folder_path}
    threading.Thread(target=_run, args=(snapshot["id"],), daemon=True).start()
    return snapshot


def _set(jid: int, **kw) -> None:
    with session_scope() as s:
        job = s.get(M4bJob, jid)
        if job:
            for k, v in kw.items():
                setattr(job, k, v)
            s.add(job)


def _run(jid: int) -> None:
    _acquire_slot()
    tmp_out = concat_path = meta_path = None
    try:
        with session_scope() as s:
            job = s.get(M4bJob, jid)
            folder = job.folder_path
            book_id = job.audiobook_id
        files = source_files(folder)
        if not files:
            _set(jid, status="error", message="no source files", finished_at=datetime.utcnow())
            return
        _set(jid, status="running", progress=5)

        # durations -> chapters (skip chapters entirely if any probe fails)
        durs = [_duration(f) for f in files]
        have_all = all(d and d > 0 for d in durs)
        base = os.path.join(folder, f".managarr-m4b-{uuid.uuid4().hex[:8]}")
        concat_path, meta_path = base + ".concat", base + ".ffmeta"
        with open(concat_path, "w") as fh:
            for f in files:
                fh.write(f"file '{_ff_escape(f)}'\n")

        num_chapters = 0
        if have_all:
            with open(meta_path, "w") as fh:
                fh.write(";FFMETADATA1\n")
                t = 0.0
                for i, (f, d) in enumerate(zip(files, durs), 1):
                    start_ms, end_ms = int(t * 1000), int((t + d) * 1000)
                    fh.write("[CHAPTER]\nTIMEBASE=1/1000\n")
                    fh.write(f"START={start_ms}\nEND={end_ms}\n")
                    fh.write(f"title={_chapter_title(f, i)}\n")
                    t += d
                    num_chapters += 1
        _set(jid, num_chapters=num_chapters or None, progress=20)

        out_name = os.path.basename(folder.rstrip("/")) + ".m4b"
        final = os.path.join(folder, out_name)
        tmp_out = base + ".m4b.part"
        cmd = [settings.FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0", "-i", concat_path]
        if num_chapters:
            cmd += ["-i", meta_path, "-map_metadata", "1", "-map_chapters", "1"]
        # -f ipod = the m4a/m4b (MP4-audio) muxer; required because the temp file's
        # ".part" extension means ffmpeg can't infer the container itself.
        cmd += ["-map", "0:a", "-c:a", "aac", "-b:a", settings.M4B_BITRATE,
                "-vn", "-movflags", "+faststart", "-f", "ipod", tmp_out]
        log.info("m4b: converting %d files in %s", len(files), folder)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60 * 60 * 6)
        if proc.returncode != 0 or not os.path.isfile(tmp_out):
            _cleanup(tmp_out)
            _set(jid, status="error", progress=0,
                 message=f"ffmpeg rc={proc.returncode}: {(proc.stderr or '')[-400:]}",
                 finished_at=datetime.utcnow())
            return

        # verify duration roughly matches the sum of the parts
        out_dur = _duration(tmp_out)
        if have_all and out_dur and abs(out_dur - sum(durs)) > _DUR_TOLERANCE:
            _cleanup(tmp_out)
            _set(jid, status="error", progress=0,
                 message=f"duration mismatch out={out_dur:.0f}s parts={sum(durs):.0f}s",
                 finished_at=datetime.utcnow())
            return

        os.replace(tmp_out, final)
        tmp_out = None
        out_size = os.path.getsize(final)
        with session_scope() as s:
            book = s.get(Audiobook, book_id) if book_id else None
            if book:
                book.has_m4b = True
                book.issues = []
                s.add(book)
        _set(jid, status="done", progress=100, output_path=final, output_size_bytes=out_size,
             finished_at=datetime.utcnow())
        notify(f"Converted to m4b: {out_name}",
               f"{len(files)} files, {num_chapters} chapters",
               kind="transcoded", media_kind="audiobook")
    except Exception as exc:
        log.exception("m4b conversion failed for job %s", jid)
        _cleanup(tmp_out)
        _set(jid, status="error", progress=0, message=str(exc), finished_at=datetime.utcnow())
    finally:
        for p in (concat_path, meta_path):
            _cleanup(p)
        _release_slot()


def _cleanup(path: Optional[str]) -> None:
    try:
        if path and os.path.isfile(path):
            os.unlink(path)
    except OSError:
        pass


def jobs(session: Session, limit: int = 50) -> list[M4bJob]:
    return session.exec(select(M4bJob).order_by(M4bJob.created_at.desc()).limit(limit)).all()


def active_for(session: Session, audiobook_id: int) -> Optional[M4bJob]:
    return session.exec(
        select(M4bJob).where(M4bJob.audiobook_id == audiobook_id,
                             M4bJob.status.in_(["queued", "running"]))
        .order_by(M4bJob.created_at.desc())
    ).first()
