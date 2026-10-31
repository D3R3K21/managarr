"""Seed default rows on first boot (idempotent)."""
from __future__ import annotations

import logging
import os

from sqlmodel import select

from .config import settings
from .db import session_scope
from .models import Indexer, QualityProfile, RootFolder

log = logging.getLogger("managarr.bootstrap")


def seed_defaults() -> None:
    with session_scope() as s:
        if not s.exec(select(RootFolder)).first():
            for kind, path in (("movie", settings.MOVIES_PATH), ("tv", settings.TV_PATH),
                               ("audiobook", settings.AUDIOBOOKS_PATH)):
                if path and os.path.isdir(path):
                    s.add(RootFolder(path=path, media_kind=kind))
            log.info("seeded default root folders from env paths")

        if not s.exec(select(Indexer)).first():
            s.add(Indexer(name="MyAnonamouse", kind="mam", enabled=True,
                          categories=["audiobook"], config={"main_cat": "13"}))
            s.add(Indexer(name="IPTorrents", kind="iptorrents", enabled=True,
                          categories=["movie", "tv"], config={}))
            log.info("seeded default indexers")

        if not s.exec(select(QualityProfile)).first():
            s.add(QualityProfile(name="Movies HD", media_kind="movie", min_seeders=2,
                                 allowed_qualities=["2160p", "1080p", "720p"],
                                 preferred_words=["x265", "hevc", "BluRay"],
                                 avoid_words=["cam", "ts", "telesync"], is_default=True))
            s.add(QualityProfile(name="TV HD", media_kind="tv", min_seeders=2,
                                 allowed_qualities=["1080p", "720p"],
                                 preferred_words=["x265", "hevc", "WEB-DL"],
                                 avoid_words=["cam", "ts"], is_default=True))
            s.add(QualityProfile(name="Audiobooks", media_kind="audiobook", min_seeders=1,
                                 preferred_words=["m4b", "unabridged"],
                                 avoid_words=["dramatized"], is_default=True))
            log.info("seeded default quality profiles")
