"""On-disk metadata sidecars.

Every imported item gets a ``managarr.json`` (our canonical, generic schema) plus
a player-compatible sidecar: Kodi/Plex ``movie.nfo`` / ``tvshow.nfo`` for video,
ABS ``metadata.opf`` for audiobooks.

``build_metadata_dict(item, kind)`` assembles the generic dict from a model row;
``write_sidecar(folder, data)`` writes everything.
"""
from __future__ import annotations

import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Optional
from xml.sax.saxutils import escape

log = logging.getLogger("managarr.sidecar")

SCHEMA_VERSION = 1


# ── generic dict assembly ─────────────────────────────────────────────────────
def build_metadata_dict(item: Any, kind: str) -> dict:
    """Assemble the canonical managarr.json dict from a model row.

    Works off the shared columns + the item's ``meta`` JSON blob (whose
    ``providers`` / ``trailers`` / ``people`` keys we surface).
    """
    meta = dict(getattr(item, "meta", None) or {})
    providers = dict(meta.get("providers") or {})
    people = list(meta.get("people") or [])
    trailers = list(meta.get("trailers") or [])
    collection = meta.get("collection") or None

    data: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "kind": kind,
        "title": getattr(item, "title", None) or "",
        "year": getattr(item, "year", None) or getattr(item, "publish_year", None),
        "overview": getattr(item, "overview", None) or "",
        "runtime_seconds": getattr(item, "runtime_seconds", None) or getattr(item, "duration_seconds", None),
        "genres": meta.get("genres") or [],
        "people": people,
        "collection": collection,
        "trailers": trailers,
        "providers": providers,
        "managarr": {
            "item_id": getattr(item, "id", None),
            "last_refreshed": datetime.utcnow().isoformat(),
        },
    }

    # Audiobook-specific niceties (author/series/narrator) surfaced into people.
    if kind == "audiobook":
        if not people:
            if getattr(item, "author_name", None):
                data["people"].append({"role": "author", "name": item.author_name,
                                       "providers": {"audible": {"asin": getattr(item, "asin", None)}}})
            if getattr(item, "narrator", None):
                data["people"].append({"role": "narrator", "name": item.narrator, "providers": {}})
        if getattr(item, "subtitle", None):
            data["subtitle"] = item.subtitle
        if getattr(item, "series_position", None) is not None and not collection:
            data["collection"] = {"name": None, "position": item.series_position}
    return data


# ── managarr.json ─────────────────────────────────────────────────────────────
def write_sidecar(folder: str, data: dict) -> None:
    """Write ``managarr.json`` + a player-native sidecar for the kind."""
    try:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "managarr.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        log.warning("failed to write managarr.json to %s: %s", folder, exc)
        return

    kind = data.get("kind")
    try:
        if kind in ("movie", "tv", "series"):
            write_nfo(folder, data, kind)
        elif kind == "audiobook":
            write_opf(folder, data)
    except OSError as exc:
        log.warning("failed to write player sidecar in %s: %s", folder, exc)


# ── Kodi/Plex NFO ─────────────────────────────────────────────────────────────
def _x(v: Any) -> str:
    return escape(str(v)) if v is not None else ""


def write_nfo(folder: str, data: dict, kind: str) -> str:
    providers = data.get("providers") or {}
    tmdb_id = (providers.get("tmdb") or {}).get("id", "")
    imdb_id = (providers.get("imdb") or {}).get("id", "")
    tvdb_id = (providers.get("tvdb") or {}).get("id", "")
    genres = "\n".join(f"  <genre>{_x(g)}</genre>" for g in (data.get("genres") or []))
    runtime_min = ""
    if data.get("runtime_seconds"):
        runtime_min = str(int(data["runtime_seconds"] // 60))
    actor_lines = "\n".join(
        f"  <actor><name>{_x(p.get('name'))}</name><role>{_x(p.get('character',''))}</role></actor>"
        for p in (data.get("people") or [])
        if p.get("role") in ("actor", "cast")
    )

    if kind == "movie":
        body = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            "<movie>\n"
            f"  <title>{_x(data.get('title'))}</title>\n"
            f"  <year>{_x(data.get('year') or '')}</year>\n"
            f"  <plot>{_x(data.get('overview'))}</plot>\n"
            f"  <runtime>{_x(runtime_min)}</runtime>\n"
            + (genres + "\n" if genres else "")
            + f"  <id>{_x(imdb_id)}</id>\n"
            + f"  <tmdbid>{_x(tmdb_id)}</tmdbid>\n"
            + (actor_lines + "\n" if actor_lines else "")
            + "</movie>\n"
        )
        name = "movie.nfo"
    else:  # tv / series
        body = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            "<tvshow>\n"
            f"  <title>{_x(data.get('title'))}</title>\n"
            f"  <year>{_x(data.get('year') or '')}</year>\n"
            f"  <plot>{_x(data.get('overview'))}</plot>\n"
            + (genres + "\n" if genres else "")
            + f"  <tvdbid>{_x(tvdb_id)}</tvdbid>\n"
            + f"  <tmdbid>{_x(tmdb_id)}</tmdbid>\n"
            + "</tvshow>\n"
        )
        name = "tvshow.nfo"

    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


# ── ABS metadata.opf ──────────────────────────────────────────────────────────
def write_opf(folder: str, data: dict) -> str:
    providers = data.get("providers") or {}
    asin = (providers.get("audible") or {}).get("asin", "")
    isbn = (providers.get("isbn") or {}).get("id", "") if isinstance(providers.get("isbn"), dict) else ""
    people = data.get("people") or []
    authors = [p["name"] for p in people if p.get("role") == "author" and p.get("name")]
    narrators = [p["name"] for p in people if p.get("role") == "narrator" and p.get("name")]
    collection = data.get("collection") or {}
    series_name = collection.get("name")
    series_pos = collection.get("position")

    creators = "\n".join(
        f'    <dc:creator opf:role="aut">{_x(a)}</dc:creator>' for a in authors
    )
    narrator_meta = "\n".join(
        f'    <meta property="role">{_x(n)}</meta>' for n in narrators
    )
    series_meta = ""
    if series_name:
        series_meta = f'    <meta name="calibre:series" content="{_x(series_name)}"/>\n'
        if series_pos is not None:
            series_meta += f'    <meta name="calibre:series_index" content="{_x(series_pos)}"/>\n'

    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:opf="http://www.idpf.org/2007/opf">\n'
        f"    <dc:title>{_x(data.get('title'))}</dc:title>\n"
        + (f"    <dc:description>{_x(data.get('overview'))}</dc:description>\n" if data.get("overview") else "")
        + (creators + "\n" if creators else "")
        + (narrator_meta + "\n" if narrator_meta else "")
        + (f'    <dc:identifier opf:scheme="ASIN">{_x(asin)}</dc:identifier>\n' if asin else "")
        + (f'    <dc:identifier opf:scheme="ISBN">{_x(isbn)}</dc:identifier>\n' if isbn else "")
        + (f"    <dc:date>{_x(data.get('year'))}</dc:date>\n" if data.get("year") else "")
        + series_meta
        + "  </metadata>\n"
        + "</package>\n"
    )
    path = os.path.join(folder, "metadata.opf")
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


# ── readers (meta-file-first import) ──────────────────────────────────────────
def read_sidecar(folder: str) -> Optional[dict]:
    """Our canonical managarr.json, if present."""
    p = os.path.join(folder, "managarr.json")
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            log.debug("read managarr.json %s failed: %s", folder, exc)
    return None


def _find_nfo(folder: str, preferred: str) -> Optional[str]:
    p = os.path.join(folder, preferred)
    if os.path.isfile(p):
        return p
    try:
        for f in os.listdir(folder):
            if f.lower().endswith(".nfo"):
                return os.path.join(folder, f)
    except OSError:
        pass
    return None


def read_nfo(folder: str, kind: str) -> Optional[dict]:
    """Parse a Kodi/Plex movie.nfo / tvshow.nfo into a partial canonical dict."""
    path = _find_nfo(folder, "movie.nfo" if kind == "movie" else "tvshow.nfo")
    if not path:
        return None
    try:
        root = ET.parse(path).getroot()
    except Exception as exc:
        log.debug("parse nfo %s failed: %s", path, exc)
        return None

    def txt(tag: str) -> Optional[str]:
        el = root.find(tag)
        return el.text.strip() if el is not None and el.text else None

    providers: dict = {}
    for uid in root.findall("uniqueid"):
        t = (uid.get("type") or "").lower()
        if uid.text and t in ("tmdb", "imdb", "tvdb"):
            providers.setdefault(t, {"id": uid.text.strip()})
    for tag, prov in (("tmdbid", "tmdb"), ("imdbid", "imdb"), ("tvdbid", "tvdb"), ("id", "tvdb")):
        v = txt(tag)
        if v and prov not in providers:
            providers[prov] = {"id": v}
    year = txt("year")
    return {
        "title": txt("title"),
        "year": int(year) if year and year.isdigit() else None,
        "overview": txt("plot") or txt("outline"),
        "providers": providers,
        "_source": os.path.basename(path),
    }


_DC = {"dc": "http://purl.org/dc/elements/1.1/"}
_OPF_NS = "{http://www.idpf.org/2007/opf}"


def read_opf(folder: str) -> Optional[dict]:
    """Parse an ABS/Calibre metadata.opf into a partial canonical dict."""
    path = os.path.join(folder, "metadata.opf")
    if not os.path.isfile(path):
        return None
    try:
        root = ET.parse(path).getroot()
    except Exception as exc:
        log.debug("parse opf %s failed: %s", path, exc)
        return None

    def dctext(tag: str) -> Optional[str]:
        el = root.find(f".//dc:{tag}", _DC)
        return el.text.strip() if el is not None and el.text else None

    providers: dict = {}
    isbn = asin = None
    for ident in root.findall(".//dc:identifier", _DC):
        scheme = (ident.get(f"{_OPF_NS}scheme") or "").upper()
        if ident.text and scheme == "ASIN":
            asin = ident.text.strip()
            providers["audible"] = {"asin": asin}
        elif ident.text and scheme == "ISBN":
            isbn = ident.text.strip()
    authors = [a.text.strip() for a in root.findall(".//dc:creator", _DC) if a.text]
    return {
        "title": dctext("title"),
        "overview": dctext("description"),
        "author": authors[0] if authors else None,
        "isbn": isbn,
        "asin": asin,
        "providers": providers,
        "_source": "metadata.opf",
    }


_ALLOWED_HTML_TAGS = {"p", "br", "b", "i", "strong", "em", "del", "u", "ul", "ol", "li", "a"}


def _sanitize_html(s: Optional[str]) -> Optional[str]:
    """Sanitize an Audible/ABS HTML description to a safe allowlist (ABS's set), so
    the frontend can render it. Disallowed tags are dropped but their text is kept;
    only safe http(s)/mailto links survive on <a>."""
    if not s:
        return s
    s = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", "", s)

    def _tag(m):
        closing, tag = m.group(1), m.group(2).lower()
        if tag not in _ALLOWED_HTML_TAGS:
            return ""  # discard the tag, keep inner text
        if closing:
            return f"</{tag}>"
        if tag == "a":
            href = re.search(r'href\s*=\s*["\']([^"\']+)["\']', m.group(0), re.I)
            url = (href.group(1).strip() if href else "")
            if re.match(r"(?i)^(https?:|mailto:)", url):
                return f'<a href="{url}" target="_blank" rel="noopener noreferrer">'
            return "<a>"
        return f"<{tag}>"

    return re.sub(r"<(/?)\s*([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>", _tag, s).strip()


# backwards-compat alias (callers import this name)
_strip_html = _sanitize_html


def read_abs_metadata_json(folder: str) -> Optional[dict]:
    """Read AudiobookShelf's native ``metadata.json`` (the richest source we have for
    audiobooks: title/authors/narrators/series/description/asin/year/genres)."""
    p = os.path.join(folder, "metadata.json")
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception as exc:
        log.debug("read metadata.json %s failed: %s", folder, exc)
        return None
    if not (d.get("title") or d.get("authors")):
        return None  # not ABS book metadata
    title = re.sub(r"\s*\((Un)?[Aa]bridged\)\s*$", "", (d.get("title") or "").strip())
    authors = d.get("authors") or []
    narrators = d.get("narrators") or []
    series = d.get("series") or []
    providers: dict = {}
    if d.get("asin"):
        providers["audible"] = {"asin": d["asin"]}
    yr = d.get("publishedYear")
    return {
        "title": title or None,
        "subtitle": d.get("subtitle"),
        "author": authors[0] if authors else None,
        "narrator": ", ".join(n for n in narrators if n) or None,
        "overview": _strip_html(d.get("description")),
        "asin": d.get("asin"),
        "isbn": d.get("isbn"),
        "publish_year": int(str(yr)[:4]) if yr and str(yr)[:4].isdigit() else None,
        "series": series[0] if series else None,
        "genres": d.get("genres"),
        "publisher": d.get("publisher"),
        "language": d.get("language"),
        "providers": providers,
        "_source": "metadata.json",
    }


def read_metadata(folder: str, kind: str) -> Optional[dict]:
    """Meta-file-first import: prefer managarr.json, then the player-native sidecar
    (NFO for video; ABS metadata.json then OPF for audiobooks). Returns a partial
    canonical dict, or None if no meta file is present."""
    # Audiobooks: ABS's metadata.json is the curated source of truth — prefer it over
    # our own managarr.json export (which may be a stale/leaner enrich snapshot).
    if kind == "audiobook":
        return read_abs_metadata_json(folder) or read_sidecar(folder) or read_opf(folder)
    sc = read_sidecar(folder)
    if sc:
        sc.setdefault("_source", "managarr.json")
        return sc
    if kind in ("movie", "tv"):
        return read_nfo(folder, kind)
    return None
