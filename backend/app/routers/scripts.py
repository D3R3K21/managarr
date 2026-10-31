"""Scripts + scheduled tasks.

CRUD for uploaded ``Script`` rows (run them in-process), and CRUD for
``ScheduledTask`` rows (cron-bound builtin actions or scripts), wired into the
live scheduler. Also exposes the list of builtin action keys + a run-now endpoint.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..auth import require_admin
from ..db import get_session
from ..models import ScheduledTask, Script, ScriptRun
from ..scheduler import runner
from ..scheduler.actions import BUILTIN_ACTIONS
from ..scheduler.scripts import run_script_inline

router = APIRouter()


# ── Script CRUD ───────────────────────────────────────────────────────────────
class ScriptBody(BaseModel):
    name: str
    language: str = "python"
    body: str = ""
    enabled: bool = True


@router.get("")
@router.get("/")
def list_scripts(session: Session = Depends(get_session)):
    return session.exec(select(Script).order_by(Script.name)).all()


@router.get("/{script_id}")
def get_script(script_id: int, session: Session = Depends(get_session)):
    sc = session.get(Script, script_id)
    if not sc:
        raise HTTPException(404, "script not found")
    return sc


@router.post("")
@router.post("/")
def create_script(body: ScriptBody, session: Session = Depends(get_session),
                  user: str = Depends(require_admin)):
    if body.language not in ("python", "bash"):
        raise HTTPException(400, "language must be python or bash")
    sc = Script(name=body.name, language=body.language, body=body.body, enabled=body.enabled)
    session.add(sc)
    session.commit()
    session.refresh(sc)
    return sc


@router.put("/{script_id}")
def update_script(script_id: int, body: ScriptBody, session: Session = Depends(get_session),
                  user: str = Depends(require_admin)):
    sc = session.get(Script, script_id)
    if not sc:
        raise HTTPException(404, "script not found")
    sc.name = body.name
    sc.language = body.language
    sc.body = body.body
    sc.enabled = body.enabled
    session.add(sc)
    session.commit()
    session.refresh(sc)
    return sc


@router.delete("/{script_id}")
def delete_script(script_id: int, session: Session = Depends(get_session),
                  user: str = Depends(require_admin)):
    sc = session.get(Script, script_id)
    if not sc:
        raise HTTPException(404, "script not found")
    session.delete(sc)
    session.commit()
    return {"deleted": script_id}


@router.post("/{script_id}/run")
def run_script_now(script_id: int, session: Session = Depends(get_session),
                   user: str = Depends(require_admin)):
    sc = session.get(Script, script_id)
    if not sc:
        raise HTTPException(404, "script not found")
    session.expunge(sc)
    run = run_script_inline(sc)
    return run


@router.get("/runs/{script_id}")
def script_runs(script_id: int, session: Session = Depends(get_session)):
    return session.exec(
        select(ScriptRun).where(ScriptRun.script_id == script_id)
        .order_by(ScriptRun.started_at.desc())
    ).all()


# ── ScheduledTask CRUD ────────────────────────────────────────────────────────
class TaskBody(BaseModel):
    name: str
    cron: str
    action: str                       # builtin action key OR "script"
    script_id: Optional[int] = None
    params: Optional[dict] = None
    enabled: bool = True


@router.get("/tasks/all")
def list_tasks(session: Session = Depends(get_session)):
    return session.exec(select(ScheduledTask).order_by(ScheduledTask.name)).all()


@router.post("/tasks")
def create_task(body: TaskBody, session: Session = Depends(get_session),
                user: str = Depends(require_admin)):
    if body.action != "script" and body.action not in BUILTIN_ACTIONS:
        raise HTTPException(400, f"unknown action {body.action!r}")
    if body.action == "script" and not body.script_id:
        raise HTTPException(400, "script_id required when action=script")
    task = ScheduledTask(name=body.name, cron=body.cron, action=body.action,
                         script_id=body.script_id, params=body.params, enabled=body.enabled)
    session.add(task)
    session.commit()
    session.refresh(task)
    try:
        runner.add_task(task)
    except Exception:
        pass
    return task


@router.put("/tasks/{task_id}")
def update_task(task_id: int, body: TaskBody, session: Session = Depends(get_session),
                user: str = Depends(require_admin)):
    task = session.get(ScheduledTask, task_id)
    if not task:
        raise HTTPException(404, "task not found")
    task.name = body.name
    task.cron = body.cron
    task.action = body.action
    task.script_id = body.script_id
    task.params = body.params
    task.enabled = body.enabled
    session.add(task)
    session.commit()
    session.refresh(task)
    try:
        runner.add_task(task)
    except Exception:
        pass
    return task


@router.delete("/tasks/{task_id}")
def delete_task(task_id: int, session: Session = Depends(get_session),
                user: str = Depends(require_admin)):
    task = session.get(ScheduledTask, task_id)
    if not task:
        raise HTTPException(404, "task not found")
    session.delete(task)
    session.commit()
    try:
        runner.remove_task(task_id)
    except Exception:
        pass
    return {"deleted": task_id}


@router.post("/tasks/{task_id}/enable")
def enable_task(task_id: int, session: Session = Depends(get_session),
                user: str = Depends(require_admin)):
    return _set_enabled(task_id, True, session)


@router.post("/tasks/{task_id}/disable")
def disable_task(task_id: int, session: Session = Depends(get_session),
                 user: str = Depends(require_admin)):
    return _set_enabled(task_id, False, session)


def _set_enabled(task_id: int, enabled: bool, session: Session):
    task = session.get(ScheduledTask, task_id)
    if not task:
        raise HTTPException(404, "task not found")
    task.enabled = enabled
    session.add(task)
    session.commit()
    session.refresh(task)
    try:
        runner.add_task(task) if enabled else runner.remove_task(task_id)
    except Exception:
        pass
    return {"id": task_id, "enabled": enabled}


# ── builtin actions + run-now ─────────────────────────────────────────────────
@router.get("/actions/builtin")
def builtin_actions():
    return {"actions": sorted(BUILTIN_ACTIONS.keys())}


@router.get("/jobs/builtin")
def builtin_jobs():
    """Default built-in scheduled jobs that run internally (e.g. the watched-movie
    mover) — shown read-only in the UI with a Run-now button."""
    return {"jobs": runner.default_jobs()}


@router.get("/jobs/registry")
def registry_jobs():
    """Every job tagged ``managarr`` in the external scheduled-jobs registry — Managarr's own
    built-ins (with last result) plus the system jobs that serve it."""
    from ..scheduler import registry
    try:
        data = registry.list_jobs()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"job history unavailable: {e}")
    return data or {"jobs": [], "unconfigured": True}


@router.get("/jobs/registry/{job_id}/runs")
def registry_job_runs(job_id: str, limit: int = 25):
    from ..scheduler import registry
    try:
        # only jobs this page lists (tagged managarr), not anything in the registry
        if not job_id.startswith("managarr.") and job_id not in {
                j["id"] for j in (registry.list_jobs() or {}).get("jobs", [])}:
            raise HTTPException(404, "not a Managarr job")
        return registry.job_runs(job_id, min(limit, 100)) or {"runs": []}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"job history unavailable: {e}")


class RunNowBody(BaseModel):
    action: str                       # builtin action key OR "script:<id>"


@router.post("/run-now")
def run_now(body: RunNowBody, user: str = Depends(require_admin)):
    try:
        return runner.run_now(body.action)
    except ValueError as e:
        raise HTTPException(400, str(e))
