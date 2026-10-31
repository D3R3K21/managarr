"""Release selection logic.

``pick_best(releases, profile)`` filters a list of normalized releases against a
QualityProfile then ranks the survivors. Ranking is **seeders desc** with
preferred_words and freeleech as tiebreakers (CONTRACT: "sort by most seeders").
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from sqlmodel import Session, select

from ..models import QualityProfile

log = logging.getLogger("managarr.decision")


def quality_profile_for(media_kind: str, session: Session) -> Optional[QualityProfile]:
    """The default profile for a kind, else any profile for that kind."""
    q = select(QualityProfile).where(QualityProfile.media_kind == media_kind)
    profiles = session.exec(q).all()
    if not profiles:
        return None
    for p in profiles:
        if p.is_default:
            return p
    return profiles[0]


def _tokens(title: str) -> str:
    return (title or "").lower()


def _matches_any(title_l: str, words: list[str] | None) -> bool:
    """Whole-token match of any ``words`` against a lowercased title.

    Release titles are dot/space/dash-delimited scene names, so we must match on
    token boundaries, NOT raw substrings: a naive ``"ts" in title`` matches the
    "ts" inside "Nigh**ts**" / "D**TS**-HD", which wrongly rejected every avoid
    word like ``ts`` (telesync) against any title containing those letters. We
    bound each word with ``(?<![a-z0-9])word(?![a-z0-9])`` so e.g. ``1080p``,
    ``ts``, ``cam`` only match as standalone tokens (``720p`` still matches in
    "...720p BluRay")."""
    if not words:
        return False
    for w in words:
        w = (w or "").lower().strip()
        if not w:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", title_l):
            return True
    return False


def _is_freeleech(r: dict) -> bool:
    if r.get("freeleech"):
        return True
    cats = [c.lower() for c in (r.get("categories") or [])]
    title_l = _tokens(r.get("title", ""))
    return "freeleech" in cats or "freeleech" in title_l


_EPISODE_RE = re.compile(r"s\d{1,2}\s*e\d{1,2}|\b\d{1,2}x\d{2}\b", re.I)
_SEASON_RE = re.compile(r"\bs\d{1,2}\b|season\s*\d|complete|\bs\d{1,2}[-–]s\d{1,2}\b", re.I)


def _is_season_pack(title: str) -> bool:
    """A whole-season / complete pack, not a single episode."""
    if _EPISODE_RE.search(title or ""):
        return False
    return bool(_SEASON_RE.search(title or ""))


# ── Title relevance (fuzzy-match guard) ───────────────────────────────────────
# The indexer's own search is a fuzzy full-text match: searching "House" returns
# "The Fall of the House of Usher". Nothing else in the acquisition path checks
# that a returned release is actually the title we asked for, so a high-seeded
# wrong show would win pick_best and get grabbed. title_relevant() is that check.

# Words that carry no identifying weight — dropped from both sides before matching
# so "The Office" matches "Office" and "A Haunting" matches "Haunting".
_TITLE_STOPWORDS = {"the", "a", "an", "of", "and"}

# Markers that begin the non-title tail of a scene release name (season/episode,
# year, resolution, source). Everything from the first such marker (that isn't at
# position 0 — protects year-like titles "1883"/"9-1-1") is not part of the show
# or movie name, so relevance matching ignores it.
_TITLE_TAIL_RE = re.compile(
    r"s\d{1,2}(?:\s*e\d{1,3})?"           # S01 / S01E02
    r"|\d{1,2}x\d{2}"                     # 1x02
    r"|season\s*\d"                       # Season 1
    r"|\b(?:19|20)\d{2}\b"               # 4-digit year
    r"|\b\d{3,4}p\b"                     # 720p / 1080p / 2160p
    r"|\bcomplete\b|\bbluray\b|\bweb[- .]?dl\b|\bwebrip\b|\bhdtv\b|\bremux\b|\bdvdrip\b",
    re.I,
)


def _title_tokens(title: str) -> list[str]:
    """Significant, order-preserving alnum tokens of a *title* (stopwords removed)."""
    toks = re.findall(r"[a-z0-9]+", (title or "").lower())
    return [t for t in toks if t not in _TITLE_STOPWORDS]


def _release_title_segment(title: str) -> str:
    """The leading show/movie-name portion of a scene release title — everything
    before the first season/episode/year/quality marker that isn't at index 0."""
    for m in _TITLE_TAIL_RE.finditer(title or ""):
        if m.start() > 0:
            return title[: m.start()]
    return title or ""


def title_relevant(release_title: str, want_title: str) -> bool:
    """True if ``release_title`` is plausibly a release of ``want_title``.

    Strict prefix/coverage: the wanted title's significant tokens must be a
    CONTIGUOUS PREFIX of the release's leading title tokens. A release that merely
    *contains* the wanted words deeper in a longer name is rejected — so searching
    "House" no longer accepts "The Fall of the House of Usher" (its title tokens
    start [fall, house, usher], and [house] is not a prefix of that). Both sides
    are reduced to their name segment first, so a trailing year/season on either
    side is ignored. Empty ``want_title`` => no constraint (can't judge)."""
    want = _title_tokens(_release_title_segment(want_title))
    if not want:
        return True
    have = _title_tokens(_release_title_segment(release_title))
    return have[: len(want)] == want


def acceptable_releases(releases: list[dict], profile: Optional[QualityProfile]) -> list[dict]:
    """Releases that pass ``profile`` (min seeders/size, avoid + allowed words). The
    shared filter for both pick_best (seeder-ranked grabs) and the quality-ranked
    upgrade path — so 'what's acceptable' is defined in exactly one place."""
    min_seeders = profile.min_seeders if profile else 1
    min_size = (profile.min_size_mb * 1024 * 1024) if (profile and profile.min_size_mb) else None
    max_size = (profile.max_size_mb * 1024 * 1024) if (profile and profile.max_size_mb) else None
    allowed = (profile.allowed_qualities if profile else None) or []
    avoid = (profile.avoid_words if profile else None) or []

    out: list[dict] = []
    for r in releases:
        title_l = _tokens(r.get("title", ""))
        seeders = int(r.get("seeders") or 0)
        size = r.get("size_bytes")

        if seeders < min_seeders:
            continue
        if min_size is not None and size is not None and size < min_size:
            continue
        if max_size is not None and size is not None and size > max_size:
            continue
        if avoid and _matches_any(title_l, avoid):
            continue
        # allowed_qualities: at least one token must appear (if specified)
        if allowed and not _matches_any(title_l, allowed):
            continue
        out.append(r)
    return out


def pick_best(releases: list[dict], profile: Optional[QualityProfile],
              prefer_pack: bool = False) -> Optional[dict]:
    """``prefer_pack`` (TV season intents) ranks season/complete packs above single
    episodes so a 'grab the season' intent never settles for one episode."""
    if not releases:
        return None

    preferred = (profile.preferred_words if profile else None) or []
    candidates = acceptable_releases(releases, profile)

    if not candidates:
        return None

    def sort_key(r: dict):
        title_l = _tokens(r.get("title", ""))
        pref = sum(1 for w in preferred if w.lower() in title_l)
        base = (
            int(r.get("seeders") or 0),   # primary: most seeders
            pref,                          # tiebreaker: preferred words
            1 if _is_freeleech(r) else 0,  # tiebreaker: freeleech
        )
        # TV season intent: season packs rank above single episodes regardless of seeders
        if prefer_pack:
            return (1 if _is_season_pack(r.get("title", "")) else 0,) + base
        return base

    candidates.sort(key=sort_key, reverse=True)
    return candidates[0]
