# Managarr

One self-hosted app to **browse, acquire, organize, and schedule** a media library
across **movies, TV, and audiobooks**: a single replacement for the
Sonarr + Radarr + Readarr + Prowlarr stack, with its own indexers, download
pipeline, importer, metadata, and library tools.

- **Backend:** Python 3.12, FastAPI (sync), SQLModel on PostgreSQL, APScheduler
- **Frontend:** React + Vite, served by the backend as static files
- **Single container:** ffmpeg/ffprobe (transcode, m4b conversion) + unar (archive extraction)

## Features

- **Library:** scan movies / TV / audiobooks from disk, rich metadata (TMDB, TVDB,
  Audible catalog, Audnexus, Google Books, OpenLibrary), collections and franchises,
  sidecar metadata.
- **Acquisition:** built-in indexers (IPTorrents for movies/TV, MyAnonamouse for
  audiobooks), quality profiles, blocklist, a throttled auto-download queue, and
  re-search for monitored-but-missing items.
- **Download client:** qBittorrent (categories, queue priority, force-start, seed ratio),
  with an optional HTTP proxy (e.g. gluetun) for all tracker traffic.
- **Import:** hardlink/copy completed downloads into the library, extracting RAR/7z sets.
- **Calendar:** upcoming releases with optional auto-grab.
- **Plex:** watchlist sync (all managed users), partial library scans after import, and a
  "watched mover" that relocates watched movies from a *New Movies* library into the main one.
- **Audiobookshelf:** triggers a rescan after audiobook imports.
- **Audible:** library sync from a [Libation](https://github.com/rmcrackan/Libation) SQLite DB.
- **Transcode:** CPU HEVC (libx265) with time windows and concurrency limits; MP3 → m4b.
- **Devices:** copy media over SMB onto devices with local storage (e.g. an Android TV box's
  local Plex library).
- **Scripts & jobs:** scheduled built-in jobs and user scripts with run history.
- **Notifications:** [ntfy](https://ntfy.sh).
- **MCP server:** lets an AI assistant query and drive the library (`ENABLE_MCP`).
- **TV-friendly UI:** D-pad focus navigation and a TV layout for Android TV WebViews.

## Quick start (Docker Compose)

```bash
cp .env.example .env              # set POSTGRES_PASSWORD, DATABASE_URL, JWT_SECRET, ADMIN_PASSWORD, API keys
$EDITOR docker-compose.yml        # point the volume mounts at your library folders
docker compose up -d --build
```

Open http://localhost:8000 and sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

For hardlink imports to work, the downloads and library folders must sit on the same
filesystem, mounted at the same paths in both Managarr and qBittorrent.

## Configuration

Everything is set through environment variables; `.env.example` lists them all with
comments, and `backend/app/config.py` is the source of truth. Any integration with no
URL set (Plex, Audiobookshelf, Radarr, ntfy, Mousehole) is disabled. Many runtime settings
(download limits, queue behaviour, etc.) can also be changed on the **Settings** page.

## Development

```bash
# backend
cd backend && python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=...   # postgresql+psycopg://USER:PASSWORD@localhost:5432/managarr
uvicorn app.main:app --reload --port 8000

# frontend (proxies /api to :8000)
cd frontend && npm install && npm run dev
```

## Disclaimer

Managarr is a library-management tool. You are responsible for complying with the laws
of your jurisdiction and the rules of any tracker you use it with.

## License

[MIT](LICENSE)
