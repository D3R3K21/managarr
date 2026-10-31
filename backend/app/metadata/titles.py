"""Audiobook title scrubbing.

Audible-sourced titles carry retail fluff that is noise on a shelf: an edition tag
("Red Rising (Unabridged)") and a series-position tail that repeats what the series
field already says ("Hell Divers VIII: King of the Wastes: The Hell Divers Series,
Book 8", "A Wanted Man - Jack Reacher Series, Book 17"). ``clean_title`` strips
exactly those and nothing else — box-set ranges ("Books 1-4"), part markers and
real subtitles stay.

Pure and dependency-free so the rules can be checked without a running service.
"""
from __future__ import annotations

import re

_EDITION = re.compile(r"\s*[(\[]\s*unabridged(?:\s+edition)?\s*[)\]]", re.I)

_NUM = (r"(?:\d+(?:\.\d+)?|[ivxlc]+|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)")

# A trailing segment naming the series and a position: "The Hell Divers Series, Book 8",
# "Commune, Book 1", "Poseidon's Children, Book 2", "The Hell Divers Series. Book 6".
# A bare "Book Three" / "Volume 1" is NOT matched — for "Commune: Book Three" or
# "Ascendant: Book 1" it is part of the title. Singular "Book" only — "Books 5 & 6"
# describes a bundle's contents and stays.
_POSITION_SEGMENT = re.compile(rf"^(?:.+?[,.]\s*|.+?\bseries\s+)(?:book|volume|vol\.?)\s+{_NUM}$", re.I)
# "Harry Potter and the Goblet of Fire, Book 4" — a position glued on with a comma
_POSITION_TAIL = re.compile(rf",\s*(?:book|volume|vol\.?)\s+{_NUM}$", re.I)
# "Commune Series 2", "The Expanse Series 4"
_SERIES_NUMBER_SEGMENT = re.compile(rf"^.*\bseries\s+{_NUM}$", re.I)
# "(A Good Girl's Guide to Murder, Book 3)" anywhere in the title
_POSITION_PAREN = re.compile(rf"\s*\((?:[^()]*?,\s*)?(?:book|volume|vol\.?)\s+{_NUM}\)", re.I)

_SEPARATORS = re.compile(r"(\s*:\s+|\s+[-–—]\s+)")


def _is_position(segment: str) -> bool:
    s = segment.strip()
    return bool(_POSITION_SEGMENT.match(s) or _SERIES_NUMBER_SEGMENT.match(s))


def clean_title(title: str | None) -> str:
    """Return ``title`` without edition tags or a trailing series-position suffix.

    Never returns an empty string: if scrubbing would leave nothing meaningful the
    original (whitespace-normalised) title is returned.
    """
    original = re.sub(r"\s+", " ", (title or "")).strip()
    s = _EDITION.sub("", original)
    s = _POSITION_PAREN.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()

    # Peel position-only segments off the end: "A: B: The X Series, Book 8" -> "A: B".
    # The first segment is the title proper and is never removed.
    parts = _SEPARATORS.split(s)  # [seg, sep, seg, sep, seg, ...]
    while len(parts) >= 3 and _is_position(parts[-1]):
        parts = parts[:-2]
    # Series-first form: "The Stormlight Archive, Book 1 - The Way of Kings".
    if len(parts) >= 3 and _is_position(parts[0]) and parts[1].strip() in ("-", "–", "—"):
        parts = parts[2:]
    s = "".join(parts).strip(" :-–—,.")
    s = _POSITION_TAIL.sub("", s).strip(" :-–—,.")

    return s if len(s) >= 2 else original
