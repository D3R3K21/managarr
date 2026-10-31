# ── Stage 1: build the React/Vite frontend ───────────────────────────────────
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python backend (serves API + built frontend, ffmpeg for transcode)
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STATIC_DIR=/app/static

# ffmpeg/ffprobe required by the scanner (codec probe) + transcode engine.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# unar (own layer, on top of the large ffmpeg layer above so that layer stays
# cache-identical and never needs re-pushing) extracts scene RAR/7z/zip release
# sets so the importer can pull the real video out of multi-part archives
# (name.rar+.r00.., name.partNN.rar, name.7z.001).
RUN apt-get update \
 && apt-get install -y --no-install-recommends unar \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY --from=frontend /fe/dist /app/static

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
