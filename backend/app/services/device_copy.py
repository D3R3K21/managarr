"""Copy library media onto an on-network device (e.g. an NVIDIA Shield running its
own Plex Media Server) over SMB, for local/offline playback.

Design notes:
- We DON'T track what's on the device. Free space and contents are read live over
  SMB (``smbprotocol``) whenever the transfer UI needs them — the device is the
  source of truth. This keeps state from drifting when the user deletes things on
  the device directly.
- A transfer is a background job with live progress (mirrors ``M4bJob``): create a
  ``DeviceCopyJob`` row, run in a daemon thread, stream the file in chunks updating
  ``copied_bytes``/``progress``, then mark done/error.
- "Remove to make space": the caller passes ``delete_paths`` (chosen in the UI from
  the live file list) which are removed on the device *before* the copy starts.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from datetime import datetime
from typing import Optional

import smbclient
from smbprotocol.exceptions import SMBOSError, SMBException

from ..config import settings
from ..db import session_scope
from ..models import DeviceCopyJob, Episode, HistoryEvent, Movie, PlexDevice, Series
from ..notify import notify
from ..pipeline.scanner import VIDEO_EXTS

log = logging.getLogger("managarr.device_copy")

CHUNK = 4 * 1024 * 1024          # 4 MiB streaming chunk
HEADROOM_MB_DEFAULT = 500        # keep at least this much free after a copy


def _headroom_bytes() -> int:
    try:
        return int(getattr(settings, "DEVICE_FREE_HEADROOM_MB", HEADROOM_MB_DEFAULT)) * 1024 * 1024
    except (TypeError, ValueError):
        return HEADROOM_MB_DEFAULT * 1024 * 1024


# ── concurrency gate (one transfer at a time by default — LAN/NFS friendly) ────
_CV = threading.Condition()
_active = 0


def _limit() -> int:
    return max(1, int(getattr(settings, "DEVICE_COPY_MAX_CONCURRENT", 1) or 1))


def _acquire() -> None:
    global _active
    with _CV:
        while _active >= _limit():
            _CV.wait()
        _active += 1


def _release() -> None:
    global _active
    with _CV:
        _active = max(0, _active - 1)
        _CV.notify()


# ── SMB path helpers ──────────────────────────────────────────────────────────
def _register(device: PlexDevice) -> None:
    # short connection timeout so an offline device fails fast (default is 60s)
    smbclient.register_session(device.host, username=device.username or "",
                               password=device.password or "", connection_timeout=8)


def _share_root(device: PlexDevice) -> str:
    return rf"\\{device.host}\{device.share}"


def _unc(device: PlexDevice, rel: str = "") -> str:
    rel = (rel or "").strip("/\\").replace("/", "\\")
    return _share_root(device) + ("\\" + rel if rel else "")


def _rel(device: PlexDevice, unc: str) -> str:
    root = _share_root(device)
    return unc[len(root):].lstrip("\\").replace("\\", "/") if unc.startswith(root) else unc


_INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _sanitize(name: str) -> str:
    return _INVALID.sub("", (name or "").strip()).rstrip(" .") or "untitled"


# ── source resolution (DB row -> local file + device-relative dest folder) ─────
def _largest_video(folder: str) -> Optional[str]:
    best, best_sz = None, -1
    try:
        for f in os.listdir(folder):
            p = os.path.join(folder, f)
            if os.path.splitext(f)[1].lower() in VIDEO_EXTS and os.path.isfile(p):
                sz = os.path.getsize(p)
                if sz > best_sz:
                    best_sz, best = sz, p
    except OSError:
        return None
    return best


def _resolve(session, device: PlexDevice, media_kind: str, item_id: int) -> dict:
    """-> {source_path, dest_rel (folder under the share), filename, title, size}."""
    if media_kind == "movie":
        m = session.get(Movie, item_id)
        if not m:
            raise ValueError(f"movie {item_id} not found")
        src = _largest_video(m.folder_path)
        if not src:
            raise ValueError("no video file found in the movie folder")
        label = f"{m.title} ({m.year})" if m.year else m.title
        dest_rel = f"{device.movies_path}/{_sanitize(label)}"
        title = label
    elif media_kind == "tv":
        ep = session.get(Episode, item_id)
        if not ep or not ep.file_path:
            raise ValueError(f"episode {item_id} has no file")
        se = session.get(Series, ep.series_id)
        series_name = _sanitize(se.title if se else "Show")
        src = ep.file_path
        dest_rel = f"{device.tv_path}/{series_name}/Season {ep.season:02d}"
        title = f"{se.title if se else 'Show'} S{ep.season:02d}E{ep.episode:02d}"
    else:
        raise ValueError(f"unsupported media_kind {media_kind!r}")
    if not os.path.isfile(src):
        raise ValueError(f"source file missing on disk: {src}")
    return {"source_path": src, "dest_rel": dest_rel, "filename": os.path.basename(src),
            "title": title, "size": os.path.getsize(src)}


# ── .plexmatch (force the device's Plex to match correctly, not guess) ─────────
def _provider_ids(meta: Optional[dict]) -> dict:
    """{imdb,tmdb,tvdb} ids from a Movie/Series meta.providers, cleaned."""
    prov = (meta or {}).get("providers") or {}
    out = {}
    for key in ("imdb", "tmdb", "tvdb"):
        v = (prov.get(key) or {}).get("id") if isinstance(prov.get(key), dict) else prov.get(key)
        v = str(v).strip() if v is not None else ""
        out[key] = v if v and v.lower() not in ("none", "null") else ""
    return out


def _guid_line(ids: dict, prefer: tuple) -> str:
    """A Plex GUID (imdb://…, tmdb://…, tvdb://…) from the first available id."""
    for key in prefer:
        v = ids.get(key)
        if not v:
            continue
        if key == "imdb" and not v.startswith("tt"):
            v = f"tt{v}" if v.isdigit() else v
        return f"{key}://{v}"
    return ""


def _plexmatch_text(kind: str, obj) -> Optional[str]:
    """`.plexmatch` contents that pin the correct title/year + provider GUID, so the
    device's Plex doesn't mis-match a loose file (e.g. 'Saw III' -> 'Luster')."""
    ids = _provider_ids(getattr(obj, "meta", None))
    prefer = ("imdb", "tmdb") if kind == "movie" else ("tvdb", "tmdb", "imdb")
    lines = [f"Title: {obj.title}"]
    if getattr(obj, "year", None):
        lines.append(f"Year: {obj.year}")
    guid = _guid_line(ids, prefer)
    if guid:
        lines.append(f"GUID: {guid}")
    return "\n".join(lines) + "\n"


def _write_plexmatch(device: PlexDevice, rel_path: str, text: str) -> None:
    with smbclient.open_file(_unc(device, rel_path), mode="w", encoding="utf-8",
                             newline="\n") as f:
        f.write(text)


# ── live device reads (free space + contents) ─────────────────────────────────
def free_space(device: PlexDevice) -> dict:
    _register(device)
    v = smbclient.stat_volume(_unc(device))
    return {"total_bytes": int(v.total_size), "free_bytes": int(v.caller_available_size)}


def list_files(device: PlexDevice) -> list[dict]:
    """Media files currently on the device (under the movie + TV subdirs), largest
    first — this is what the 'make room' picker deletes from."""
    _register(device)
    out: list[dict] = []
    for base in (device.movies_path, device.tv_path):
        top = _unc(device, base)
        try:
            for dirpath, _dirs, files in smbclient.walk(top):
                for fn in files:
                    if fn.startswith("."):
                        continue
                    full = dirpath + "\\" + fn
                    try:
                        sz = smbclient.stat(full).st_size
                    except (SMBException, OSError):
                        sz = 0
                    out.append({"path": _rel(device, full), "name": fn, "size": int(sz)})
        except (SMBException, OSError):
            continue  # subdir may not exist yet
    out.sort(key=lambda x: x["size"], reverse=True)
    return out


def delete_paths(device: PlexDevice, rel_paths: list[str]) -> int:
    """Delete files (device-relative paths) to free space. Prunes now-empty parent
    dirs up to the media root. Returns count deleted."""
    _register(device)
    n = 0
    roots = {device.movies_path.strip("/"), device.tv_path.strip("/")}
    for rp in rel_paths or []:
        rp = rp.strip("/")
        # safety: only inside the configured media subdirs
        if not any(rp == r or rp.startswith(r + "/") for r in roots):
            log.warning("refusing to delete outside media dirs: %s", rp)
            continue
        try:
            smbclient.remove(_unc(device, rp))
            n += 1
        except (SMBException, OSError) as exc:
            log.warning("delete failed for %s: %s", rp, exc)
            continue
        # prune empty parent dirs (stop at the media root)
        parent = rp.rsplit("/", 1)[0] if "/" in rp else ""
        while parent and parent not in roots:
            try:
                smbclient.rmdir(_unc(device, parent))
            except (SMBException, OSError):
                break
            parent = parent.rsplit("/", 1)[0] if "/" in parent else ""
    return n


def plan(device: PlexDevice, media_kind: str, item_id: int) -> dict:
    """Everything the transfer UI needs for one item+device: the media size, the
    device's free space, whether it fits with headroom, and the on-device file list
    for the make-room picker."""
    with session_scope() as s:
        info = _resolve(s, device, media_kind, item_id)
    space = free_space(device)
    media_size = info["size"]
    needs_room = (space["free_bytes"] - media_size) < _headroom_bytes()
    return {
        "device_id": device.id,
        "media_size": media_size,
        "filename": info["filename"],
        "title": info["title"],
        "free_bytes": space["free_bytes"],
        "total_bytes": space["total_bytes"],
        "headroom_bytes": _headroom_bytes(),
        "needs_room": needs_room,
        "files": list_files(device),
    }


def test_device(device: PlexDevice) -> dict:
    try:
        space = free_space(device)
        return {"ok": True, **space}
    except (SMBException, OSError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


# ── transfer job ──────────────────────────────────────────────────────────────
def start_copy(device_id: int, media_kind: str, item_id: int,
               delete_paths_first: Optional[list[str]] = None) -> dict:
    with session_scope() as s:
        device = s.get(PlexDevice, device_id)
        if not device:
            raise ValueError(f"device {device_id} not found")
        if not device.enabled:
            raise ValueError("device is disabled")
        info = _resolve(s, device, media_kind, item_id)
        job = DeviceCopyJob(device_id=device_id, media_kind=media_kind, item_id=item_id,
                            title=info["title"], source_path=info["source_path"],
                            dest_path=f"{info['dest_rel']}/{info['filename']}",
                            status="queued", total_bytes=info["size"], copied_bytes=0)
        s.add(job)
        s.commit()
        s.refresh(job)
        snap = {"id": job.id, "device_id": device_id, "title": job.title,
                "status": job.status, "total_bytes": job.total_bytes}
    threading.Thread(target=_run, args=(snap["id"], list(delete_paths_first or [])),
                     daemon=True).start()
    return snap


def _set(jid: int, **kw) -> None:
    with session_scope() as s:
        job = s.get(DeviceCopyJob, jid)
        if job:
            for k, v in kw.items():
                setattr(job, k, v)
            s.add(job)


def _run(jid: int, del_paths: list[str]) -> None:
    _acquire()
    try:
        with session_scope() as s:
            job = s.get(DeviceCopyJob, jid)
            device = s.get(PlexDevice, job.device_id)
            src = job.source_path
            dest_rel = job.dest_path.rsplit("/", 1)[0]
            filename = job.dest_path.rsplit("/", 1)[-1]
            total = job.total_bytes or (os.path.getsize(src) if os.path.isfile(src) else 0)
            media_kind = job.media_kind
            title = job.title
            # .plexmatch contents (device Plex matches correctly instead of guessing)
            pm_text = pm_rel = None
            try:
                if media_kind == "movie":
                    mv = s.get(Movie, job.item_id)
                    if mv:
                        pm_text, pm_rel = _plexmatch_text("movie", mv), f"{dest_rel}/.plexmatch"
                elif media_kind == "tv":
                    ep = s.get(Episode, job.item_id)
                    se = s.get(Series, ep.series_id) if ep else None
                    if se:  # series-level, so all seasons share one match
                        pm_text = _plexmatch_text("tv", se)
                        pm_rel = f"{dest_rel.rsplit('/', 1)[0]}/.plexmatch"
            except Exception:  # noqa: BLE001
                pm_text = pm_rel = None
            # detach device fields we need (session closes after this block)
            dev_snap = PlexDevice(**{k: getattr(device, k) for k in
                                     ("id", "host", "share", "movies_path", "tv_path",
                                      "username", "password", "domain", "name")})
        _set(jid, status="running", progress=1)
        _register(dev_snap)

        if del_paths:
            freed = delete_paths(dev_snap, del_paths)
            log.info("device_copy: freed %d file(s) before transfer", freed)

        smbclient.makedirs(_unc(dev_snap, dest_rel), exist_ok=True)
        if pm_text and pm_rel:
            try:
                _write_plexmatch(dev_snap, pm_rel, pm_text)
                log.info("device_copy: wrote %s", pm_rel)
            except (SMBException, OSError) as exc:
                log.warning("device_copy: .plexmatch write failed: %s", exc)
        dest_unc = _unc(dev_snap, f"{dest_rel}/{filename}")
        tmp_unc = dest_unc + ".part"
        copied = 0
        with open(src, "rb") as fsrc, smbclient.open_file(tmp_unc, mode="wb") as fdst:
            while True:
                buf = fsrc.read(CHUNK)
                if not buf:
                    break
                fdst.write(buf)
                copied += len(buf)
                pct = int(copied * 100 / total) if total else 0
                _set(jid, copied_bytes=copied, progress=min(99, max(1, pct)))
        # atomic-ish finalize: remove any old target, rename .part -> final
        try:
            smbclient.remove(dest_unc)
        except (SMBException, OSError):
            pass
        smbclient.rename(tmp_unc, dest_unc)

        _set(jid, status="done", progress=100, copied_bytes=copied,
             finished_at=datetime.utcnow())
        with session_scope() as s:
            s.add(HistoryEvent(kind="copied", media_kind=media_kind, title=title,
                               message=f"Copied to {dev_snap.name}"))
        notify(f"Copied to {dev_snap.name}", title, kind="copied", media_kind=media_kind)
    except Exception as exc:  # noqa: BLE001
        log.exception("device copy failed for job %s", jid)
        # best-effort cleanup of the partial file
        try:
            with session_scope() as s:
                job = s.get(DeviceCopyJob, jid)
                dev = s.get(PlexDevice, job.device_id) if job else None
            if dev and job:
                _register(dev)
                smbclient.remove(_unc(dev, job.dest_path + ".part"))
        except Exception:  # noqa: BLE001
            pass
        _set(jid, status="error", message=str(exc)[:500], finished_at=datetime.utcnow())
    finally:
        _release()


def jobs(session, limit: int = 50) -> list[DeviceCopyJob]:
    from sqlmodel import select
    return session.exec(
        select(DeviceCopyJob).order_by(DeviceCopyJob.created_at.desc()).limit(limit)).all()
