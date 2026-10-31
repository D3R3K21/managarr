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
docker compose up -d --build
docker compose logs managarr | grep "generated"    # first-run admin password
```

Open http://localhost:8000 and log in as `admin` with the password from the logs
(also saved to `data/config/admin_password`). That's all it takes to try it: Postgres is
bundled, and library/download folders default to `./data/*`.

## Configuration

All settings live in one env file:

```bash
cp .env.example .env
$EDITOR .env
docker compose up -d
```

`.env.example` lists every setting with its default and a comment. Anything you leave
out keeps its default. The ones you'll usually set:

| Setting | What it's for |
|---|---|
| `MOVIES_DIR`, `TV_DIR`, `AUDIOBOOKS_DIR`, `DOWNLOADS_DIR`, … | Host folders to mount. Keep downloads and libraries on one filesystem so imports can hardlink. |
| `POSTGRES_PASSWORD` | Database password, used by both containers. Set it **before** first start: Postgres stores it in its volume. |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Built-in admin login. With no password set, one is generated on first start. |
| `QBT_HOST`, `QBT_USERNAME`, `QBT_PASSWORD` | qBittorrent Web UI. |
| `TMDB_API_KEY`, `TVDB_API_KEY` | Movie/TV metadata. |
| `PLEX_URL`/`PLEX_TOKEN`, `ABS_URL`/`ABS_TOKEN`, `NTFY_URL` | Optional integrations; blank = off. |

`JWT_SECRET` is generated and kept in the config folder if you don't set one. To use your
own Postgres, set `POSTGRES_HOST`/`POSTGRES_PORT` (or a full `DATABASE_URL`) and run the
image without the bundled `postgres` service. Many runtime options (download limits, queue
behaviour) can also be changed on the **Settings** page.

## Development

```bash
# backend
cd backend && python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export POSTGRES_HOST=localhost POSTGRES_PASSWORD=... CONFIG_DIR=./data/config
uvicorn app.main:app --reload --port 8000

# frontend (proxies /api to :8000)
cd frontend && npm install && npm run dev
```

## Disclaimer

Managarr is a library-management tool. You are responsible for complying with the laws
of your jurisdiction and the rules of any tracker you use it with.

## License

[MIT](LICENSE)
