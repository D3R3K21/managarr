"""System health checks for the dashboard.

``checks(session)`` returns a list of ``{name, status, detail}`` dicts where
``status`` is one of ``ok | warn | error``. Notable check: the **gluetun VPN
guard** — compares the egress IP seen through the proxied client (VPN) vs the
direct client; if they're equal the VPN tunnel is leaking and tracker traffic
would expose the home IP, so we flag it as an error.
"""
from __future__ import annotations

import logging
import shutil
from typing import Optional

from sqlalchemy import text
from sqlmodel import Session, select

from ..config import settings
from ..http import client, proxied_client
from ..indexers.registry import get_indexers
from ..models import Indexer
from ..pipeline.qbit import QBitClient

log = logging.getLogger("managarr.health")


def _check(name: str, status: str, detail: str = "") -> dict:
    return {"name": name, "status": status, "detail": detail}


def _egress_ip(c) -> Optional[str]:
    try:
        with c:
            r = c.get("https://api.ipify.org", params={"format": "text"})
            r.raise_for_status()
            return (r.text or "").strip() or None
    except Exception as exc:
        log.debug("egress ip lookup failed: %s", exc)
        return None


def _check_qbit() -> dict:
    # qBittorrent auto-authenticates the cluster pod subnet (AuthSubnetWhitelist),
    # so login may issue no SID. Probe the API directly instead.
    try:
        ver = QBitClient().app_version()
        if ver:
            return _check("qBittorrent", "ok", f"reachable (v{ver})")
        return _check("qBittorrent", "error", "API not responding")
    except Exception as exc:
        return _check("qBittorrent", "error", f"unreachable: {exc}")


def _check_mam() -> dict:
    """MAM auth is the direct dynamic-seedbox ``mam_id`` (settings.MAM_COOKIE);
    Mousehole is only a fallback. Liveness of the actual session is covered by the
    'Indexer: MyAnonamouse' check (a live search)."""
    if (settings.MAM_COOKIE or "").strip():
        return _check("MAM session", "ok", "mam_id configured (dynamic seedbox)")
    base = (settings.MOUSEHOLE_URL or "").rstrip("/")
    if not base:
        return _check("MAM session", "warn", "no MAM_COOKIE and no Mousehole configured")
    try:
        with client(timeout=8) as c:
            data = c.get(f"{base}/state").json()
        cookie = (data or {}).get("currentCookie") or ""
        if cookie:
            return _check("MAM session", "ok", "via Mousehole currentCookie")
        return _check("MAM session", "warn", "Mousehole reachable but no cookie set")
    except Exception as exc:
        return _check("MAM session", "error", f"no MAM_COOKIE; Mousehole unreachable: {exc}")


def _check_indexers(session: Session) -> list[dict]:
    """Lightweight config check — deliberately does NOT hit the trackers. A live
    search per health poll would hammer MAM (rate-limited, ban risk) and churn the
    rotating mam_id. Verify liveness via the per-indexer Test button or a real search."""
    out: list[dict] = []
    rows = session.exec(select(Indexer).where(Indexer.enabled == True)).all()  # noqa: E712
    for row in rows:
        cfg = row.config or {}
        if row.kind == "mam":
            has = bool(cfg.get("cookie") or settings.MAM_COOKIE)
            out.append(_check(f"Indexer: {row.name}", "ok" if has else "warn",
                              "session cookie configured" if has else "no MAM cookie set"))
        elif row.kind == "iptorrents":
            has = bool(cfg.get("cookie") or settings.IPT_COOKIE)
            out.append(_check(f"Indexer: {row.name}", "ok" if has else "warn",
                              "cookie configured" if has else "no IPTorrents cookie set"))
        else:
            out.append(_check(f"Indexer: {row.name}", "ok", "enabled"))
    if not rows:
        out.append(_check("Indexers", "warn", "none enabled"))
    return out


def _check_vpn_guard() -> dict:
    """The gluetun guard: VPN egress IP must DIFFER from the direct egress IP."""
    if not settings.GLUETUN_PROXY_URL:
        return _check("VPN guard (gluetun)", "warn", "GLUETUN_PROXY_URL not set")
    vpn_ip = _egress_ip(proxied_client(timeout=12))
    direct_ip = _egress_ip(client(timeout=12))
    if vpn_ip is None:
        return _check("VPN guard (gluetun)", "error", "could not reach internet via VPN proxy")
    if direct_ip is None:
        return _check("VPN guard (gluetun)", "warn",
                      f"VPN egress {vpn_ip}; direct egress unknown")
    if vpn_ip == direct_ip:
        return _check("VPN guard (gluetun)", "error",
                      f"LEAK: VPN and direct egress identical ({vpn_ip})")
    return _check("VPN guard (gluetun)", "ok",
                  f"VPN {vpn_ip} != direct {direct_ip}")


def _check_plex() -> dict:
    if not settings.PLEX_URL:
        return _check("Plex", "warn", "PLEX_URL not set")
    try:
        with client(timeout=8) as c:
            r = c.get(f"{settings.PLEX_URL.rstrip('/')}/identity")
        if r.status_code < 500:
            return _check("Plex", "ok", f"HTTP {r.status_code}")
        return _check("Plex", "warn", f"HTTP {r.status_code}")
    except Exception as exc:
        return _check("Plex", "error", f"unreachable: {exc}")


def _check_disk(name: str, path: str) -> dict:
    try:
        usage = shutil.disk_usage(path)
        free_gb = usage.free / 1024**3
        pct_free = (usage.free / usage.total * 100) if usage.total else 0
        status = "ok" if free_gb > 50 else ("warn" if free_gb > 10 else "error")
        return _check(f"Free space: {name}", status,
                      f"{free_gb:.1f} GiB free ({pct_free:.0f}%) on {path}")
    except Exception as exc:
        return _check(f"Free space: {name}", "error", f"{path}: {exc}")


def _check_db(session: Session) -> dict:
    try:
        session.execute(text("SELECT 1"))
        return _check("Database", "ok", "connected")
    except Exception as exc:
        return _check("Database", "error", f"query failed: {exc}")


def _check_mam_ratio() -> dict:
    """Informational MAM ratio note — we don't have a verified ratio endpoint here,
    so surface a reminder rather than a hard number."""
    return _check("MAM ratio", "warn",
                  "ratio not polled here — keep an eye on seeding (MAM is ratio-gated)")


def checks(session: Session) -> list[dict]:
    results: list[dict] = []
    results.append(_check_qbit())
    results.append(_check_mam())
    results.extend(_check_indexers(session))
    results.append(_check_vpn_guard())
    results.append(_check_plex())
    results.append(_check_disk("movies", settings.MOVIES_PATH))
    results.append(_check_disk("tv", settings.TV_PATH))
    results.append(_check_disk("audiobooks", settings.AUDIOBOOKS_PATH))
    results.append(_check_disk("downloads", settings.DOWNLOADS_PATH))
    results.append(_check_db(session))
    results.append(_check_mam_ratio())
    return results
