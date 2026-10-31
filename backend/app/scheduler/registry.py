"""Report Managarr's scheduled jobs to an external scheduled-jobs registry (optional).

Managarr keeps running its own APScheduler (the jobs are app logic and belong
in-process); this module only *tells* the registry about them so every job on
the box — host cron, systemd timers, k8s CronJobs, Managarr — shows up in one
place with run history and a plain-language result. It is also how the Jobs
tab here shows the system jobs that matter to Managarr (tag ``managarr``:
the media image auto-updater, the qBittorrent missing-files fixer, the
Libation scan).

The registry is an external HTTP service (``CLAUTO_JOBS_URL``), reached
with a token that can only touch ``/api/jobs*`` (``CLAUTO_JOBS_TOKEN``). Both
unset => everything here is a silent no-op. Reporting is best-effort and
fire-and-forget on a worker thread: a registry outage must never slow down or
fail a Managarr job.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from ..config import settings

log = logging.getLogger("managarr.registry")

APP = "managarr"
TAG = "managarr"

# What each built-in action does, in the words the Jobs page shows.
DESCRIPTIONS = {
    "import_completed": "Import finished downloads from qBittorrent into the library.",
    "process_download_queue": "Start queued downloads as download slots free up.",
    "enqueue_auto_downloads": "Queue missing movies and episodes for automatic download.",
    "sync_calendar": "Refresh the release calendar from TMDB/TVDB.",
    "calendar_auto_grab": "Grab calendar releases that have become available.",
    "sync_watchlist": "Pull new items from the Plex watchlist.",
    "move_watched_movies": "Move watched movies from New Movies into the main Movies library.",
    "transcode_process": "Work through the transcode/optimise queue.",
    "scan_all": "Rescan every library so file state stays accurate after outside changes.",
    "refresh_airing_series": "Refresh episode lists for continuing series (new seasons → calendar + queue).",
    "clean_abs_titles": "Tidy retail fluff out of new Audiobookshelf titles.",
    "rss_search": "Search RSS feeds for wanted items and upgrades.",
    "upgrade_search": "Look for quality upgrades of existing items.",
    "transcode_scan": "Find files that should be transcoded.",
    "refresh_metadata_all": "Refresh metadata for the whole library.",
    "rebuild_collections": "Rebuild movie collections and audiobook series.",
    "sync_audible": "Sync the Audible library.",
}

EXTRA_TAGS = {
    "import_completed": ["qbittorrent"], "process_download_queue": ["qbittorrent"],
    "enqueue_auto_downloads": ["qbittorrent"], "move_watched_movies": ["plex"],
    "sync_watchlist": ["plex"], "clean_abs_titles": ["audiobookshelf", "audiobooks"],
    "sync_audible": ["audiobooks"],
}


def configured() -> bool:
    return bool(settings.CLAUTO_JOBS_URL and settings.CLAUTO_JOBS_TOKEN)


def _client(timeout: float = 5.0) -> httpx.Client:
    return httpx.Client(base_url=settings.CLAUTO_JOBS_URL.rstrip("/"), timeout=timeout,
                        headers={"Authorization": f"Bearer {settings.CLAUTO_JOBS_TOKEN}"})


def _bg(fn, *args) -> None:
    if configured():
        threading.Thread(target=fn, args=args, daemon=True).start()


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _title(action: str) -> str:
    return action.replace("_", " ").capitalize()


# ── registration ──────────────────────────────────────────────────────────────
def register(default_jobs: list[dict], tasks: list[dict]) -> None:
    """Announce every job Managarr schedules. ``default_jobs`` = runner.default_jobs()
    rows ({action, schedule, minutes}); ``tasks`` = DB ScheduledTask rows."""
    jobs = []
    for j in default_jobs:
        a = j["action"]
        jobs.append({
            "key": a, "title": _title(a), "description": DESCRIPTIONS.get(a, ""),
            "schedule": j["schedule"], "tags": [TAG, "media", *EXTRA_TAGS.get(a, [])],
            # frequent pollers keep failures + status changes only
            "record": "changes" if (j.get("minutes") or 10**6) <= 30 else "all",
            "command": f"builtin action {a}",
        })
    for t in tasks:
        what = f"script #{t['script_id']}" if t["action"] == "script" else f"builtin action {t['action']}"
        jobs.append({
            "key": f"task-{t['id']}", "title": t["name"], "schedule": f"cron {t['cron']} ({settings.TZ})",
            "description": f"Scheduled task: runs {what}.", "tags": [TAG, "media"],
            "enabled": t["enabled"], "command": what,
        })
    _bg(_post, "/api/jobs/external/register", {"app": APP, "jobs": jobs})


def _post(path: str, body: dict) -> None:
    try:
        with _client() as c:
            r = c.post(path, json=body)
            if r.status_code >= 400:
                log.warning("registry %s -> %s %s", path, r.status_code, r.text[:200])
    except Exception as e:  # noqa: BLE001 — best effort, never raise into a job
        log.debug("registry %s unreachable: %s", path, e)


# ── run reporting ─────────────────────────────────────────────────────────────
def summarize(result: Any) -> str:
    """Turn an action's return value into one readable line.
    {"checked": 12, "grabbed": 0} -> "checked 12 · grabbed 0"."""
    if result is None:
        return "done"
    if isinstance(result, (int, float)):
        return f"{result} item(s)"
    if isinstance(result, str):
        return result[:500]
    if isinstance(result, (list, tuple)):
        return f"{len(result)} item(s)"
    if isinstance(result, dict):
        parts = []
        for k, v in result.items():
            label = str(k).replace("_", " ")
            if isinstance(v, dict):
                inner = ", ".join(f"{str(ik).replace('_', ' ')} {iv}" for ik, iv in v.items()
                                  if not isinstance(iv, (dict, list)))
                parts.append(f"{label}: {inner}" if inner else label)
            elif isinstance(v, (list, tuple)):
                parts.append(f"{label} {len(v)}")
            elif isinstance(v, bool):
                parts.append(f"{label} {'yes' if v else 'no'}")
            else:
                parts.append(f"{label} {v}")
        return " · ".join(parts)[:500] or "done"
    return str(result)[:500]


def report(key: str, started: str, status: str, summary: str, trigger: str = "schedule") -> None:
    """status: success | failed | skipped."""
    _bg(_post, "/api/jobs/runs", {
        "job_id": f"{APP}.{key}", "started_at": started, "finished_at": _now(),
        "status": status, "summary": summary, "trigger": trigger,
    })


class Run:
    """``with registry.Run("scan_all") as r: r.summary = summarize(fn())`` —
    records start/finish/status; an exception marks the run failed and re-raises."""

    def __init__(self, key: str, trigger: str = "schedule"):
        self.key, self.trigger, self.summary, self.status = key, trigger, "", "success"

    def __enter__(self):
        self.started = _now()
        return self

    def __exit__(self, et, ev, _tb):
        if et is not None:
            self.status, self.summary = "failed", f"{et.__name__}: {ev}"[:500]
        report(self.key, self.started, self.status, self.summary, self.trigger)
        return False


# ── reads for the Jobs page ───────────────────────────────────────────────────
def list_jobs(tag: str = TAG) -> Optional[dict]:
    if not configured():
        return None
    with _client(8.0) as c:
        r = c.get("/api/jobs", params={"tag": tag})
        r.raise_for_status()
        return r.json()


def job_runs(job_id: str, limit: int = 25) -> Optional[dict]:
    if not configured():
        return None
    with _client(8.0) as c:
        r = c.get(f"/api/jobs/{job_id}/runs", params={"limit": limit})
        r.raise_for_status()
        return r.json()
