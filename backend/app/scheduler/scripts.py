"""Uploaded-script worker (in-process).

Runs user-uploaded Python / bash scripts and records a ``ScriptRun``.

SECURITY TRADEOFF (documented intentionally): scripts run **in-process** on the
backend host with the same uid as the app and only a lightly-restricted env. This
is acceptable ONLY because Managarr is a single-user homelab tool sitting behind
auth + Cloudflare Access, and the operator is the sole script author. There is no
sandbox: a script can read/modify the library and call the local API. Do NOT
expose script upload to untrusted users. A future hardening would run scripts in a
short-lived container / nsjail with seccomp + a read-only rootfs.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime

from ..config import settings
from ..db import session_scope
from ..models import Script, ScriptRun

log = logging.getLogger("managarr.scripts")

_TIMEOUT_SECONDS = 600


def _restricted_env() -> dict:
    """A minimal env: keep PATH + a couple of handles, drop secrets."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": settings.SCRIPTS_DIR,
        "MANAGARR_API": "http://localhost:8000",
        "PYTHONUNBUFFERED": "1",
        "TZ": settings.TZ,
    }
    return env


def run_script_inline(script: Script) -> ScriptRun:
    """Execute ``script`` synchronously and return the persisted ScriptRun."""
    os.makedirs(settings.SCRIPTS_DIR, exist_ok=True)

    # create the ScriptRun up front (status=running) so the UI can show it
    with session_scope() as s:
        run = ScriptRun(script_id=script.id, status="running")
        s.add(run)
        s.commit()
        s.refresh(run)
        run_id = run.id

    suffix = ".py" if script.language == "python" else ".sh"
    interpreter = [sys.executable] if script.language == "python" else ["/bin/bash"]

    status = "success"
    exit_code = None
    stdout = ""
    stderr = ""
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=suffix, dir=settings.SCRIPTS_DIR)
        with os.fdopen(fd, "w") as f:
            f.write(script.body or "")
        proc = subprocess.run(
            interpreter + [tmp_path],
            cwd=settings.SCRIPTS_DIR,
            env=_restricted_env(),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
        exit_code = proc.returncode
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        status = "success" if exit_code == 0 else "error"
    except subprocess.TimeoutExpired as exc:
        status = "error"
        stderr = f"script timed out after {_TIMEOUT_SECONDS}s\n" + (exc.stderr or "" if isinstance(exc.stderr, str) else "")
        log.warning("script %s timed out", script.id)
    except Exception as exc:
        status = "error"
        stderr = f"failed to run script: {exc}"
        log.exception("script %s run failed", script.id)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # truncate giant outputs to keep the DB sane
    stdout = stdout[-100_000:]
    stderr = stderr[-100_000:]

    with session_scope() as s:
        run = s.get(ScriptRun, run_id)
        run.status = status
        run.exit_code = exit_code
        run.stdout = stdout
        run.stderr = stderr
        run.finished_at = datetime.utcnow()
        s.add(run)
        s.commit()
        s.refresh(run)
        s.expunge(run)
        return run


def run_script(script_id: int) -> ScriptRun:
    """Load a Script by id and execute it. Used by the scheduler + the run route."""
    with session_scope() as s:
        script = s.get(Script, script_id)
        if not script:
            raise ValueError(f"script {script_id} not found")
        s.expunge(script)
    return run_script_inline(script)
