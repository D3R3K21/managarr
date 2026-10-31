"""APScheduler BackgroundScheduler wiring.

Registers default jobs (import/rss/calendar/watchlist/transcode) and binds any
enabled ``ScheduledTask`` rows (cron -> CronTrigger) to either a builtin action
or ``run_script``. Guards against double-start (idempotent ``start_scheduler``).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import select

from ..config import settings
from ..db import session_scope
from ..models import ScheduledTask
from . import registry
from .actions import BUILTIN_ACTIONS
from .scripts import run_script

log = logging.getLogger("managarr.scheduler")

_scheduler: Optional[BackgroundScheduler] = None

# default in-process jobs: (id, action_key, IntervalTrigger kwargs)
# NOTE: the auto-download ENQUEUE job is added separately in _add_default_jobs
# because its interval is runtime-configurable (AUTO_DOWNLOAD_SYNC_INTERVAL_MINUTES).
# ``process_download_queue`` (the dispatcher) ticks every 2 min to fill freed slots.
_DEFAULT_JOBS = [
    ("default:import_completed", "import_completed", {"minutes": 2}),
    ("default:download_queue", "process_download_queue", {"minutes": 2}),
    ("default:sync_calendar", "sync_calendar", {"hours": 6}),
    ("default:calendar_auto_grab", "calendar_auto_grab", {"hours": 1}),
    ("default:sync_watchlist", "sync_watchlist", {"minutes": 30}),
    ("default:move_watched_movies", "move_watched_movies", {"minutes": 15}),
    ("default:transcode_process", "transcode_process", {"minutes": 10}),
    # keep has_file/library state honest — files get moved/deleted outside Managarr
    # (watched-mover, Plex deletes), and a stale library blocks re-grabs
    ("default:scan_all", "scan_all", {"hours": 12}),
    # keep continuing series' episode lists current (new seasons -> calendar + enqueue)
    ("default:refresh_airing_series", "refresh_airing_series", {"hours": 24}),
    # tidy retail fluff out of new Audiobookshelf titles ("(Unabridged)", ": X Series, Book 3")
    ("default:clean_abs_titles", "clean_abs_titles", {"hours": 24}),
]


def default_jobs() -> list[dict]:
    """The built-in interval jobs (for display in the UI), with a human schedule."""
    def human(kw: dict) -> str:
        for unit in ("minutes", "hours", "days"):
            if unit in kw:
                n = kw[unit]
                return f"every {n} {unit[:-1] if n == 1 else unit}"
        return "interval"
    def minutes(kw: dict) -> int:
        return int(kw.get("minutes", 0) + 60 * kw.get("hours", 0) + 1440 * kw.get("days", 0))
    jobs = [{"action": action, "schedule": human(kw), "minutes": minutes(kw)}
            for _id, action, kw in _DEFAULT_JOBS]
    m = _enqueue_interval_minutes()
    jobs.append({"action": "enqueue_auto_downloads", "schedule": human({"minutes": m}), "minutes": m})
    return jobs


def _register_with_registry() -> None:
    """Tell the external scheduled-jobs registry what we run (no-op when unconfigured)."""
    try:
        with session_scope() as s:
            tasks = [{"id": t.id, "name": t.name, "cron": t.cron, "action": t.action,
                      "script_id": t.script_id, "enabled": t.enabled}
                     for t in s.exec(select(ScheduledTask)).all()]
        registry.register(default_jobs(), tasks)
    except Exception:
        log.exception("registry registration failed")


def _wrap_action(action_key: str):
    """Wrap a builtin action so exceptions are logged, not swallowed by APScheduler."""
    fn = BUILTIN_ACTIONS[action_key]

    def _run():
        try:
            with registry.Run(action_key) as r:
                result = fn()
                r.summary = registry.summarize(result)
            log.info("scheduled action %s -> %s", action_key, result)
        except Exception:
            log.exception("scheduled action %s failed", action_key)
    return _run


def _wrap_task(task_id: int, action: str, script_id: Optional[int]):
    """Wrap a DB ScheduledTask: run its builtin action or its script, record status."""
    def _run():
        status = "success"
        started = registry._now()
        summary = ""
        try:
            if action == "script" and script_id:
                run = run_script(script_id)
                summary = f"script run #{run.id}: {run.status}"
                if run.status not in ("success", "ok"):
                    status = "error"
            elif action in BUILTIN_ACTIONS:
                summary = registry.summarize(BUILTIN_ACTIONS[action]())
            else:
                log.warning("scheduled task %s has unknown action %r", task_id, action)
                status, summary = "error", f"unknown action {action!r}"
        except Exception as e:
            log.exception("scheduled task %s failed", task_id)
            status, summary = "error", f"{type(e).__name__}: {e}"
        registry.report(f"task-{task_id}", started, "success" if status == "success" else "failed", summary)
        try:
            with session_scope() as s:
                row = s.get(ScheduledTask, task_id)
                if row:
                    row.last_run = datetime.utcnow()
                    row.last_status = status
                    s.add(row)
        except Exception:
            log.exception("failed to record last_run for task %s", task_id)
    return _run


def _enqueue_interval_minutes() -> int:
    """Auto-download enqueue cadence: Settings override, else config default."""
    from .. import settings_store
    raw = settings_store.get_setting("AUTO_DOWNLOAD_SYNC_INTERVAL_MINUTES", None)
    try:
        return int(raw) if raw not in (None, "") else int(settings.AUTO_DOWNLOAD_SYNC_INTERVAL_MINUTES)
    except (TypeError, ValueError):
        return int(settings.AUTO_DOWNLOAD_SYNC_INTERVAL_MINUTES)


def _add_default_jobs(sched: BackgroundScheduler) -> None:
    for job_id, action_key, kw in _DEFAULT_JOBS:
        sched.add_job(_wrap_action(action_key), IntervalTrigger(**kw),
                      id=job_id, replace_existing=True, max_instances=1,
                      coalesce=True, misfire_grace_time=300)
    # enqueue scan: runtime-configurable interval (re-read on reschedule_sync())
    sched.add_job(_wrap_action("enqueue_auto_downloads"),
                  IntervalTrigger(minutes=_enqueue_interval_minutes()),
                  id="default:enqueue_auto_downloads", replace_existing=True,
                  max_instances=1, coalesce=True, misfire_grace_time=300)


def reschedule_sync() -> int:
    """Re-apply the auto-download enqueue interval from settings (call after the
    Settings page changes AUTO_DOWNLOAD_SYNC_INTERVAL_MINUTES). Returns the minutes."""
    mins = _enqueue_interval_minutes()
    if _scheduler:
        _scheduler.add_job(_wrap_action("enqueue_auto_downloads"),
                           IntervalTrigger(minutes=mins),
                           id="default:enqueue_auto_downloads", replace_existing=True,
                           max_instances=1, coalesce=True, misfire_grace_time=300)
    return mins


def _load_db_tasks(sched: BackgroundScheduler) -> int:
    """(Re)load enabled ScheduledTask rows as cron jobs. Returns count loaded."""
    # clear previously-loaded db tasks
    for job in sched.get_jobs():
        if job.id and job.id.startswith("task:"):
            sched.remove_job(job.id)

    loaded = 0
    with session_scope() as s:
        tasks = s.exec(select(ScheduledTask).where(ScheduledTask.enabled == True)).all()  # noqa: E712
        rows = [(t.id, t.name, t.cron, t.action, t.script_id) for t in tasks]
    for task_id, name, cron, action, script_id in rows:
        try:
            trigger = CronTrigger.from_crontab(cron, timezone=settings.TZ)
        except Exception:
            log.exception("bad cron %r for task %s (%s); skipping", cron, task_id, name)
            continue
        sched.add_job(_wrap_task(task_id, action, script_id), trigger,
                      id=f"task:{task_id}", replace_existing=True, max_instances=1,
                      coalesce=True, misfire_grace_time=300)
        loaded += 1
    log.info("loaded %d scheduled task(s)", loaded)
    return loaded


def start_scheduler() -> BackgroundScheduler:
    """Start the scheduler (idempotent)."""
    global _scheduler
    if _scheduler and _scheduler.running:
        log.info("scheduler already running")
        return _scheduler
    sched = _scheduler or BackgroundScheduler(timezone=settings.TZ)
    _scheduler = sched
    _add_default_jobs(sched)
    _load_db_tasks(sched)
    if not sched.running:
        sched.start()
    log.info("scheduler started (tz=%s)", settings.TZ)
    _register_with_registry()
    return sched


def reload_tasks() -> int:
    """Re-read ScheduledTask rows from the DB into the live scheduler."""
    if not _scheduler:
        start_scheduler()
    n = _load_db_tasks(_scheduler)
    _register_with_registry()
    return n


def add_task(task: ScheduledTask) -> None:
    """Add/replace a single ScheduledTask in the live scheduler."""
    if not _scheduler:
        start_scheduler()
    if not task.enabled:
        remove_task(task.id)
        return
    try:
        trigger = CronTrigger.from_crontab(task.cron, timezone=settings.TZ)
    except Exception:
        log.exception("bad cron %r for task %s", task.cron, task.id)
        return
    _scheduler.add_job(_wrap_task(task.id, task.action, task.script_id), trigger,
                       id=f"task:{task.id}", replace_existing=True, max_instances=1,
                       coalesce=True, misfire_grace_time=300)
    _register_with_registry()


def remove_task(task_id: int) -> None:
    if not _scheduler:
        return
    try:
        _scheduler.remove_job(f"task:{task_id}")
    except Exception:
        pass
    _register_with_registry()


def run_now(action_or_script: str) -> dict:
    """Run a builtin action immediately (synchronously) and return its result.
    Accepts a builtin action key or ``"script:<id>"``."""
    if action_or_script.startswith("script:"):
        sid = int(action_or_script.split(":", 1)[1])
        run = run_script(sid)
        return {"script_id": sid, "run_id": run.id, "status": run.status}
    if action_or_script in BUILTIN_ACTIONS:
        with registry.Run(action_or_script, trigger="manual") as r:
            result = BUILTIN_ACTIONS[action_or_script]()
            r.summary = registry.summarize(result)
        return {"action": action_or_script, "result": result}
    raise ValueError(f"unknown action {action_or_script!r}")


def get_scheduler() -> Optional[BackgroundScheduler]:
    return _scheduler
