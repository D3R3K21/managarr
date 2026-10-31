"""Central configuration. All values come from environment variables
(see `.env.example`). Integrations with no URL configured are disabled.
Secrets (api keys, tokens) have no defaults.
"""
from __future__ import annotations

import os
from functools import lru_cache


def _b(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes", "on")


class Settings:
    # ── Core ────────────────────────────────────────────────────────────────
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://managarr@localhost:5432/managarr",
    )
    JWT_SECRET: str = os.getenv("JWT_SECRET", "dev-insecure-change-me")
    ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "")  # required: set via env
    TZ: str = os.getenv("TZ", "UTC")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # ── Library roots (mounted volumes) ────────────────────────────────────────
    MOVIES_PATH: str = os.getenv("MOVIES_PATH", "/movies")
    # New movies download here (a separate "New Movies" library); the watched-mover relocates
    # them to MOVIES_PATH once watched in Plex (qBit follows via setLocation).
    NEW_MOVIES_PATH: str = os.getenv("NEW_MOVIES_PATH", "/new-movies")
    TV_PATH: str = os.getenv("TV_PATH", "/tv")
    AUDIOBOOKS_PATH: str = os.getenv("AUDIOBOOKS_PATH", "/audiobooks")
    # Languages an audiobook match may be in. A Spanish edition carries the same title and author
    # as the English one, so without this the catalogue can hand back a translation and the whole
    # library entry (title, narrator, description) turns Spanish. Blank accepts any language.
    METADATA_LANGUAGES: str = os.getenv("METADATA_LANGUAGES", "english")
    DOWNLOADS_PATH: str = os.getenv("DOWNLOADS_PATH", "/downloads")

    # ── Playback devices (SMB copy targets: on-device Plex libraries) ─────────
    # At most this many transfers run at once (LAN/NFS friendly; copies are big).
    DEVICE_COPY_MAX_CONCURRENT: int = int(os.getenv("DEVICE_COPY_MAX_CONCURRENT", "1"))
    # Keep at least this much free on a device after a copy; below it the UI forces
    # the user to remove something first.
    DEVICE_FREE_HEADROOM_MB: int = int(os.getenv("DEVICE_FREE_HEADROOM_MB", "500"))

    # ── Download client (qBittorrent, behind gluetun) ────────────────────────
    # Master kill-switch for the qBittorrent integration. When false, NOTHING is
    # ever sent to qBit (manual or automatic). Runtime-overridable via DOWNLOADS_ENABLED.
    DOWNLOADS_ENABLED: str = os.getenv("DOWNLOADS_ENABLED", "true")
    # Gate for the AUTOMATIC grab paths only (RSS re-search, watchlist sync, calendar
    # auto-grab). When false, those scheduled jobs detect/sync but never download;
    # manual "Grab" actions still work. Runtime-overridable via AUTO_DOWNLOAD_ENABLED.
    AUTO_DOWNLOAD_ENABLED: str = os.getenv("AUTO_DOWNLOAD_ENABLED", "true")
    QBT_HOST: str = os.getenv("QBT_HOST", "localhost")
    QBT_PORT: str = os.getenv("QBT_PORT", "8080")
    QBT_USERNAME: str = os.getenv("QBT_USERNAME", "")
    QBT_PASSWORD: str = os.getenv("QBT_PASSWORD", "")
    QBT_CATEGORY: str = os.getenv("QBT_CATEGORY", "managarr")
    QBT_SEED_RATIO: str = os.getenv("QBT_SEED_RATIO", "")  # blank = qbit default
    # Queue priority for torrents Managarr sends: none|top|bottom. Default 'none' =
    # qBit's behavior (append to bottom). Runtime-overridable via the
    # QBT_QUEUE_PRIORITY setting on the Settings page.
    QBT_QUEUE_PRIORITY: str = os.getenv("QBT_QUEUE_PRIORITY", "none")
    # Force-start torrents Managarr sends (bypass queue + active-download limit).
    # Orthogonal to QBT_QUEUE_PRIORITY; runtime-overridable via QBT_FORCE_START.
    QBT_FORCE_START: str = os.getenv("QBT_FORCE_START", "false")

    # ── Indexers ──────────────────────────────────────────────────────────────
    # MAM via Mousehole session cookie, routed through gluetun HTTP proxy so the
    # request IP matches the MAM session (anti-leak / anti-ban).
    # Direct MAM session cookie (the `mbsc` value, with or without the prefix),
    # grabbed from the VPN-side browser. Takes precedence over Mousehole. Must be
    # created from the same ASN Managarr egresses on (the gluetun VPN exit).
    MAM_COOKIE: str = os.getenv("MAM_COOKIE", "")
    MOUSEHOLE_URL: str = os.getenv("MOUSEHOLE_URL", "")
    GLUETUN_PROXY_URL: str = os.getenv("GLUETUN_PROXY_URL", "")
    IPT_COOKIE: str = os.getenv("IPT_COOKIE", "")  # uid=...; pass=... session cookie

    # ── Metadata providers (API keys) ───────
    TMDB_API_KEY: str = os.getenv("TMDB_API_KEY", "")
    TVDB_API_KEY: str = os.getenv("TVDB_API_KEY", "")
    # Audnexus + Audible catalog + Google Books + OpenLibrary need no key.

    # ── Radarr (optional one-time movie-franchise migration source) ───────────
    # Source for movie *franchises* (full part list + posters + which we
    # own), local so collections build without hammering TMDB. Audiobook series
    # come from our own DB (ABS metadata.json already ingested as scanned_series).
    RADARR_URL: str = os.getenv("RADARR_URL", "")
    RADARR_API_KEY: str = os.getenv("RADARR_API_KEY", "")

    # ── Plex (token reads all managed users' watchlists) ───────────────────────
    PLEX_URL: str = os.getenv("PLEX_URL", "")
    PLEX_TOKEN: str = os.getenv("PLEX_TOKEN", "")
    # Audiobookshelf — poke it to rescan its book library after an audiobook import.
    ABS_URL: str = os.getenv("ABS_URL", "")          # e.g. http://audiobookshelf:80
    ABS_TOKEN: str = os.getenv("ABS_TOKEN", "")      # ABS API key (admin)
    ABS_LIBRARY: str = os.getenv("ABS_LIBRARY", "")  # optional library NAME; blank = first book library

    # ── Audible/Libation (Audible library sync) ────────────────────────────────
    LIBATION_DB_PATH: str = os.getenv("LIBATION_DB_PATH", "/db/LibationContext.db")

    # ── Notifications (ntfy) ──────────────────────────────────────────────────
    # Optional external scheduled-jobs registry (see app/scheduler/registry.py). Unset = no reporting.
    CLAUTO_JOBS_URL: str = os.getenv("CLAUTO_JOBS_URL", "")
    CLAUTO_JOBS_TOKEN: str = os.getenv("CLAUTO_JOBS_TOKEN", "")
    NTFY_URL: str = os.getenv("NTFY_URL", "")
    NTFY_TOPIC: str = os.getenv("NTFY_TOPIC", "managarr")
    NTFY_TOKEN: str = os.getenv("NTFY_TOKEN", "")

    # ── Transcode (HEVC, CPU-only libx265; NFS-safe defaults) ─────────────────
    TRANSCODE_CRF: int = int(os.getenv("TRANSCODE_CRF", "21"))
    TRANSCODE_PRESET: str = os.getenv("TRANSCODE_PRESET", "medium")
    TRANSCODE_MAX_CONCURRENT: int = int(os.getenv("TRANSCODE_MAX_CONCURRENT", "1"))
    # Throttled auto-download queue (the "watched"/auto-fill path). All runtime-
    # overridable via the Settings key/value store (PUT /api/settings).
    # Max auto-downloads live in qBit at once (manual grabs bypass this cap).
    AUTO_DOWNLOAD_MAX_CONCURRENT: int = int(os.getenv("AUTO_DOWNLOAD_MAX_CONCURRENT", "5"))
    # How often the enqueue scan tops up the queue from monitored-missing items.
    AUTO_DOWNLOAD_SYNC_INTERVAL_MINUTES: int = int(os.getenv("AUTO_DOWNLOAD_SYNC_INTERVAL_MINUTES", "30"))
    # Re-search this many times before parking an item as "releaseless".
    AUTO_DOWNLOAD_MAX_ATTEMPTS: int = int(os.getenv("AUTO_DOWNLOAD_MAX_ATTEMPTS", "5"))
    TRANSCODE_THREADS: int = int(os.getenv("TRANSCODE_THREADS", "4"))  # cap CPU
    TRANSCODE_WINDOW: str = os.getenv("TRANSCODE_WINDOW", "")  # e.g. "01:00-07:00"; blank=anytime
    FFMPEG_BIN: str = os.getenv("FFMPEG_BIN", "ffmpeg")
    FFPROBE_BIN: str = os.getenv("FFPROBE_BIN", "ffprobe")
    # AAC bitrate for MP3 -> m4b audiobook conversion (speech, so 64k is plenty).
    M4B_BITRATE: str = os.getenv("M4B_BITRATE", "64k")
    # Max simultaneous m4b conversions (ffmpeg-heavy). Runtime-overridable in Settings.
    M4B_MAX_CONCURRENT: int = int(os.getenv("M4B_MAX_CONCURRENT", "1"))

    # ── Behaviour flags ───────────────────────────────────────────────────────
    # Scanner: skip per-file ffprobe by default (slow over NFS; codec is only needed
    # for transcode, which probes its own candidates). Commit in batches for progress.
    SCAN_PROBE_CODEC: bool = _b("SCAN_PROBE_CODEC", False)
    SCAN_BATCH: int = int(os.getenv("SCAN_BATCH", "100"))

    ENABLE_SCHEDULER: bool = _b("ENABLE_SCHEDULER", True)
    ENABLE_MCP: bool = _b("ENABLE_MCP", True)
    SCRIPTS_DIR: str = os.getenv("SCRIPTS_DIR", "/config/scripts")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
