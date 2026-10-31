"""Unified domain model.

A single polymorphic-ish MediaItem is avoided in favour of explicit tables per
kind (cleaner queries), but they share the generic-metadata pattern: canonical
columns + a `meta` JSON blob whose `providers` key holds provider-specific
ids/urls. The on-disk sidecar (`managarr.json`) is an export of `meta`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Any

from sqlalchemy import BigInteger, Column, JSON, Text
from sqlmodel import SQLModel, Field


def _json_col():
    # Portable JSON column (Postgres -> jsonb-ish, SQLite -> TEXT json).
    return Field(default=None, sa_column=Column(JSON))


def _bigint_col():
    # File/torrent sizes in BYTES exceed 32-bit INTEGER (>2.1 GB), so use BIGINT.
    return Field(default=None, sa_column=Column(BigInteger))


# ── kv + auth ─────────────────────────────────────────────────────────────────
class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str = Field(sa_column=Column(Text))


class User(SQLModel, table=True):
    """A login account. The env ADMIN_USERNAME is the bootstrap superadmin and is
    NOT stored here; these are the additional admin-managed accounts."""
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str                     # pbkdf2$iterations$salt_hex$hash_hex
    role: str = "user"                     # admin | user
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── People (authors; reusable for "by same author") ───────────────────────────
class Person(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    role: str = "author"           # author | narrator
    asin: Optional[str] = None     # Audible/Audnexus author ASIN
    image_url: Optional[str] = None
    description: Optional[str] = Field(default=None, sa_column=Column(Text))
    meta: Optional[dict[str, Any]] = _json_col()
    last_fetched: Optional[datetime] = None


# ── Collections (franchise / book-series) ─────────────────────────────────────
class Collection(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = "movie"            # movie(franchise) | audiobook(series)
    name: str = Field(index=True)
    overview: Optional[str] = Field(default=None, sa_column=Column(Text))
    poster_url: Optional[str] = None
    monitored: bool = False        # "watched" — opt-in to auto-fill missing members
    meta: Optional[dict[str, Any]] = _json_col()   # providers: {tmdb_collection_id, audnexus_asin,...}


class CollectionMember(SQLModel, table=True):
    """An EXPECTED member of a collection. owned=False => 'missing'."""
    id: Optional[int] = Field(default=None, primary_key=True)
    collection_id: int = Field(foreign_key="collection.id", index=True)
    title: str
    position: Optional[float] = None
    year: Optional[int] = None
    owned: bool = False
    item_kind: Optional[str] = None       # movie | audiobook
    item_id: Optional[int] = None         # FK to owned item if present
    release_date: Optional[str] = None
    meta: Optional[dict[str, Any]] = _json_col()   # provider ids + cover_url


# ── Movies ────────────────────────────────────────────────────────────────────
class Movie(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(index=True)
    year: Optional[int] = None
    folder_path: str
    overview: Optional[str] = Field(default=None, sa_column=Column(Text))
    poster_url: Optional[str] = None
    runtime_seconds: Optional[int] = None
    collection_id: Optional[int] = Field(default=None, foreign_key="collection.id")
    collection_order: Optional[float] = None
    monitored: bool = True
    has_file: bool = True
    file_size_bytes: Optional[int] = _bigint_col()
    video_codec: Optional[str] = None     # from ffprobe (for transcode targeting)
    issues: Optional[list[str]] = _json_col()
    meta: Optional[dict[str, Any]] = _json_col()   # providers: {tmdb,imdb,...}, trailers[]
    last_scanned: datetime = Field(default_factory=datetime.utcnow)


# ── TV ────────────────────────────────────────────────────────────────────────
class Series(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(index=True)
    year: Optional[int] = None
    folder_path: str
    overview: Optional[str] = Field(default=None, sa_column=Column(Text))
    poster_url: Optional[str] = None
    status: Optional[str] = None          # continuing | ended
    monitored: bool = True
    issues: Optional[list[str]] = _json_col()
    meta: Optional[dict[str, Any]] = _json_col()
    last_scanned: datetime = Field(default_factory=datetime.utcnow)


class Episode(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    series_id: int = Field(foreign_key="series.id", index=True)
    season: int
    episode: int
    title: Optional[str] = None
    air_date: Optional[str] = None        # ISO date (calendar)
    file_path: Optional[str] = None       # None = missing/stub
    monitored: bool = True
    has_file: bool = False
    video_codec: Optional[str] = None
    meta: Optional[dict[str, Any]] = _json_col()


# ── Audiobooks ────────────────────────────────────────────────────────────────
class Audiobook(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(index=True)
    subtitle: Optional[str] = None
    author_id: Optional[int] = Field(default=None, foreign_key="person.id")
    author_name: Optional[str] = None     # denormalised for display/search
    narrator: Optional[str] = None
    folder_path: str
    overview: Optional[str] = Field(default=None, sa_column=Column(Text))
    cover_url: Optional[str] = None
    asin: Optional[str] = None
    isbn: Optional[str] = None
    publish_year: Optional[int] = None
    duration_seconds: Optional[int] = None
    collection_id: Optional[int] = Field(default=None, foreign_key="collection.id")
    series_position: Optional[float] = None
    monitored: bool = True
    has_audio: bool = False        # any audio present (m4b/mp3/m4a/…) — i.e. NOT missing
    has_m4b: bool = False          # has a single-file .m4b (format/quality flag, NOT presence)
    issues: Optional[list[str]] = _json_col()
    meta: Optional[dict[str, Any]] = _json_col()  # providers: {audible,google_books,openlibrary}
    last_scanned: datetime = Field(default_factory=datetime.utcnow)


# ── Acquisition ───────────────────────────────────────────────────────────────
class Indexer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    kind: str                              # mam | iptorrents
    enabled: bool = True
    categories: Optional[list[str]] = _json_col()
    config: Optional[dict[str, Any]] = _json_col()


class QualityProfile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    media_kind: str                        # movie | tv | audiobook
    min_seeders: int = 1
    min_size_mb: Optional[int] = None
    max_size_mb: Optional[int] = None
    allowed_qualities: Optional[list[str]] = _json_col()   # ["1080p","720p"]
    preferred_words: Optional[list[str]] = _json_col()
    avoid_words: Optional[list[str]] = _json_col()
    is_default: bool = False


class RootFolder(SQLModel, table=True):
    """A library directory to scan. Multiple per media_kind are allowed."""
    id: Optional[int] = Field(default=None, primary_key=True)
    path: str
    media_kind: str                        # movie | tv | audiobook


class Grab(SQLModel, table=True):
    """A release we decided to download -> tracked through qBit -> import."""
    id: Optional[int] = Field(default=None, primary_key=True)
    media_kind: str                        # movie | tv | audiobook
    item_id: Optional[int] = None          # owned item once imported
    title: str                             # release title
    indexer: str
    download_url: Optional[str] = None
    infohash: Optional[str] = None
    save_path: str = ""
    category: str = "managarr"
    size_bytes: Optional[int] = _bigint_col()
    seeders: Optional[int] = None
    source: str = "manual"                 # manual | rss | watchlist | calendar | missing
    state: str = "grabbed"                 # grabbed | downloading | importing | imported | failed
    message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Blocklist(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    infohash: Optional[str] = Field(default=None, index=True)
    title: Optional[str] = None
    reason: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── Calendar ──────────────────────────────────────────────────────────────────
class ReleaseEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    media_kind: str
    title: str
    date: str                              # ISO date
    event_type: str = "release"            # air | digital | physical | street | release
    item_id: Optional[int] = None
    auto_grab: bool = True                 # grab when date passes & release appears
    grabbed: bool = False
    meta: Optional[dict[str, Any]] = _json_col()


# ── Scheduler + scripts ───────────────────────────────────────────────────────
class ScheduledTask(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    cron: str                              # standard 5-field cron
    action: str                            # builtin action key OR "script"
    script_id: Optional[int] = Field(default=None, foreign_key="script.id")
    params: Optional[dict[str, Any]] = _json_col()
    enabled: bool = True
    last_run: Optional[datetime] = None
    last_status: Optional[str] = None


class Script(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    language: str = "python"               # python | bash
    body: str = Field(sa_column=Column(Text))
    enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ScriptRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    script_id: int = Field(foreign_key="script.id", index=True)
    status: str = "running"                # running | success | error
    exit_code: Optional[int] = None
    stdout: Optional[str] = Field(default=None, sa_column=Column(Text))
    stderr: Optional[str] = Field(default=None, sa_column=Column(Text))
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None


# ── Transcode ─────────────────────────────────────────────────────────────────
class TranscodeJob(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    media_kind: str                        # movie | tv
    item_id: Optional[int] = None
    source_path: str
    target_codec: str = "hevc"
    status: str = "candidate"              # candidate | approved | running | done | error | skipped
    reason: Optional[str] = None           # why flagged
    source_size_bytes: Optional[int] = _bigint_col()
    output_size_bytes: Optional[int] = _bigint_col()
    message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None


# ── Audiobook MP3 -> chapterized m4b conversion ───────────────────────────────
class M4bJob(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    audiobook_id: Optional[int] = Field(default=None, foreign_key="audiobook.id")
    folder_path: str
    status: str = "queued"                 # queued | running | done | error
    progress: int = 0                      # 0-100 (file index / total)
    num_files: Optional[int] = None
    num_chapters: Optional[int] = None
    source_size_bytes: Optional[int] = _bigint_col()
    output_size_bytes: Optional[int] = _bigint_col()
    output_path: Optional[str] = None
    message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None


# ── Playback devices (SMB copy targets: on-device Plex libraries) ─────────────
class PlexDevice(SQLModel, table=True):
    """An on-network device with local storage (e.g. an NVIDIA Shield running its
    own Plex Media Server) that we can copy media onto over SMB for local playback.

    We deliberately DON'T track what's on the device — free space and contents are
    read live over SMB at transfer time (the device is the source of truth)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    location: Optional[str] = None          # room / where it lives
    host: str                               # IP or hostname (SMB share)
    share: str = "internal"                 # SMB share name
    movies_path: str = "Movies"             # subdir (within share) for movie copies
    tv_path: str = "TV Shows"               # subdir for TV episode copies
    username: str = ""
    password: str = ""                      # SMB password — write-only in API responses
    domain: str = "WORKGROUP"
    enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DeviceCopyJob(SQLModel, table=True):
    """A single file transfer to a PlexDevice, with live progress (mirrors M4bJob)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    device_id: int = Field(foreign_key="plexdevice.id", index=True)
    media_kind: str                         # movie | tv
    item_id: int
    title: str
    source_path: str
    dest_path: str = ""                     # remote path on the share
    status: str = "queued"                  # queued | running | done | error
    progress: int = 0                       # 0-100
    total_bytes: Optional[int] = _bigint_col()
    copied_bytes: Optional[int] = _bigint_col()
    message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None


class DevicePairing(SQLModel, table=True):
    """OAuth-style device-authorization grant (RFC 8628): a TV/remote-typing-averse
    device gets a short user_code + secret device_code; a logged-in user approves the
    user_code from another device; the device then polls and receives a JWT — no
    credentials typed on the TV."""
    id: Optional[int] = Field(default=None, primary_key=True)
    device_code: str = Field(index=True)    # long secret held by the requesting device
    user_code: str = Field(index=True)      # short human code (no dashes here), unique among pending
    status: str = "pending"                 # pending | approved | claimed | expired
    device_label: Optional[str] = None      # descriptor the device sent (e.g. "Managarr TV")
    username: Optional[str] = None          # approving user's identity (baked into the token)
    role: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    approved_at: Optional[datetime] = None


# ── Unified activity log ──────────────────────────────────────────────────────
class HistoryEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str                              # grabbed | imported | failed | transcoded | scan | watchlist | error
    media_kind: Optional[str] = None
    title: Optional[str] = None
    message: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    meta: Optional[dict[str, Any]] = _json_col()


# ── Throttled auto-download queue ─────────────────────────────────────────────
class DownloadQueueItem(SQLModel, table=True):
    """An automatic download *intent*, throttled into qBit by the dispatcher.

    Distinct from Grab: a queue item is created BEFORE a release is chosen (the
    indexer search runs at dispatch time). Manual grabs NEVER appear here — they
    go straight to qBit, so only auto items are rate-limited. States:
      queued      -> waiting for a free slot
      active      -> a Grab was sent to qBit (counts against the concurrency cap)
      done        -> the linked Grab imported
      no_release  -> decide() found nothing acceptable; retried after next_retry_at
      parked      -> attempts exhausted; the library item is flagged releaseless
                     and awaits the user (manual delete / "Search again"). Never
                     auto-deleted.
      failed      -> the Grab failed to download/import
    Only ``active`` rows count toward AUTO_DOWNLOAD_MAX_CONCURRENT.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    media_kind: str                        # movie | tv | audiobook
    item_id: Optional[int] = None          # FK to the owned shell item (library row)
    title: str                             # display
    query: str                             # search string handed to indexers
    save_path: str = ""
    author: Optional[str] = None           # path/search hint (audiobooks)
    series: Optional[str] = None           # path/search hint (tv/books)
    season: Optional[int] = None           # path/search hint (tv)
    source: str = "missing"                # missing | watchlist | calendar | watched_collection
    priority: int = 30                     # lower = sooner (watchlist 10, collection 20, rss 30)
    state: str = "queued"                  # queued | active | done | no_release | parked | failed
    grab_id: Optional[int] = Field(default=None, foreign_key="grab.id")
    infohash: Optional[str] = None
    attempts: int = 0
    next_retry_at: Optional[datetime] = None
    last_error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    dispatched_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
