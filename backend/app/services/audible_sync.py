"""Audible library sync via Libation.

Libation manages Audible authentication + download/organization. This module
reconciles Libation's SQLite database with Managarr's Audiobook table:
  - Queries Libation's database for owned books
  - Matches against existing Audiobooks (by title + author)
  - Creates new records for unmatched books
  - Logs events (imported, duplicate, match_failed)
  - Triggers ABS scan after each import

Config: LIBATION_DB_PATH (default /db/LibationContext.db).
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, select

from ..config import settings
from ..models import Audiobook, HistoryEvent, Person
from . import abs

log = logging.getLogger("managarr.audible_sync")

SYNC_PROGRESS: dict = {"running": False, "progress": "", "error": None}


def audible_configured() -> bool:
    libation_db = Path(settings.LIBATION_DB_PATH or "/db/LibationContext.db")
    return libation_db.exists() and bool(settings.AUDIOBOOKS_PATH)


def _get_libation_db() -> sqlite3.Connection | None:
    """Connect to Libation's SQLite database."""
    libation_db = Path(settings.LIBATION_DB_PATH or "/db/LibationContext.db")
    if not libation_db.exists():
        log.warning("Libation database not found at %s", libation_db)
        return None
    try:
        conn = sqlite3.connect(str(libation_db), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as exc:
        log.error("failed to connect to Libation DB: %s", exc)
        return None


def _get_libation_books(conn: sqlite3.Connection) -> list[dict]:
    """Fetch all owned books from Libation's database.

    Queries the Products table for books. Returns list of dicts with:
    {asin, title, author, narrator, series, series_position, publish_year, cover_url}.
    """
    try:
        cursor = conn.cursor()
        # Get all active products (books) from Libation
        # Libation schema: Products table with columns like Title, AuthorName, etc.
        cursor.execute("""
            SELECT
                COALESCE(AsinString, Asin) AS asin,
                Title AS title,
                AuthorName AS author,
                NarratorName AS narrator,
                SeriesName AS series,
                SeriesPosition AS series_position,
                CAST(SUBSTR(COALESCE(DatePublished, ''), 1, 4) AS INTEGER) AS publish_year,
                ImageUrl AS cover_url
            FROM Products
            WHERE (IsDeleted = 0 OR IsDeleted IS NULL)
            ORDER BY DateAdded DESC NULLS LAST
        """)
        books = []
        for row in cursor.fetchall():
            if row["title"]:  # Skip entries with no title
                books.append({
                    "asin": row["asin"] or "",
                    "title": row["title"],
                    "author": row["author"] or "Unknown",
                    "narrator": row["narrator"],
                    "series": row["series"],
                    "series_position": row["series_position"],
                    "publish_year": row["publish_year"],
                    "cover_url": row["cover_url"],
                })
        log.info("fetched %d books from Libation database", len(books))
        return books
    except sqlite3.OperationalError as exc:
        log.error("Libation database query failed (schema mismatch?): %s", exc)
        return []
    except Exception as exc:
        log.error("failed to query Libation books: %s", exc)
        return []


def _find_author(name: str, session: Session) -> int | None:
    """Get or create a Person record for an author."""
    if not name or not name.strip():
        return None
    name = name.strip()
    try:
        stmt = select(Person).where(
            Person.name == name,
            Person.role == "author"
        )
        person = session.exec(stmt).first()
        if person:
            return person.id
        person = Person(name=name, role="author")
        session.add(person)
        session.flush()
        return person.id
    except Exception as exc:
        log.warning("failed to find/create author %s: %s", name, exc)
        return None


def sync(session: Session) -> dict:
    """Run sync: fetch Libation books, reconcile with DB, log events.

    Returns summary dict: {imported, duplicate, match_failed, error}.
    """
    SYNC_PROGRESS.update(running=True, progress="connecting to Libation DB", error=None)

    if not audible_configured():
        err = "Audible sync not configured (LIBATION_DB_PATH missing or not readable)"
        log.warning(err)
        SYNC_PROGRESS.update(running=False, error=err)
        return {"imported": 0, "duplicate": 0, "match_failed": 0, "error": err}

    conn = _get_libation_db()
    if not conn:
        err = "failed to connect to Libation database"
        SYNC_PROGRESS.update(running=False, error=err)
        return {"imported": 0, "duplicate": 0, "match_failed": 0, "error": err}

    try:
        SYNC_PROGRESS["progress"] = "fetching books from Libation"
        libation_books = _get_libation_books(conn)

        if not libation_books:
            log.info("no books found in Libation database")
            SYNC_PROGRESS.update(running=False, progress="no books in Libation")
            return {"imported": 0, "duplicate": 0, "match_failed": 0, "error": None}

        imported = 0
        duplicate = 0
        match_failed = 0

        for i, book in enumerate(libation_books):
            SYNC_PROGRESS["progress"] = f"[{i+1}/{len(libation_books)}] {book['title'][:50]}"

            try:
                # Exact match by title + author
                stmt = select(Audiobook).where(
                    Audiobook.title == book["title"],
                    Audiobook.author_name == book["author"]
                )
                existing = session.exec(stmt).first()

                if existing:
                    log.debug("audiobook already in library: %s", book["title"])
                    duplicate += 1
                    evt = HistoryEvent(
                        kind="audible_duplicate",
                        media_kind="audiobook",
                        title=book["title"],
                        message=f"Already in library (author: {book['author']})",
                        meta={"asin": book["asin"], "author": book["author"]}
                    )
                    session.add(evt)
                    continue

                # Create new Audiobook record
                author_id = _find_author(book["author"], session)

                # Folder path: Libation organizes as
                # {author}/{series}/{series# - title} or {author}/{title}
                folder_path = str(Path(settings.AUDIOBOOKS_PATH) / book["author"])
                if book.get("series"):
                    series_folder = book["series"]
                    if book.get("series_position"):
                        series_folder = f"{series_folder}/{book['series_position']}"
                    folder_path = str(Path(folder_path) / series_folder)

                audiobook = Audiobook(
                    title=book["title"],
                    author_id=author_id,
                    author_name=book["author"],
                    narrator=book.get("narrator"),
                    folder_path=folder_path,
                    cover_url=book.get("cover_url"),
                    asin=book["asin"],
                    publish_year=book.get("publish_year"),
                    collection_id=None,
                    series_position=book.get("series_position"),
                    monitored=True,
                    has_audio=False,
                    has_m4b=False,
                    meta={
                        "providers": {
                            "audible": {
                                "asin": book["asin"],
                                "synced_from": "libation",
                                "sync_date": datetime.utcnow().isoformat()
                            }
                        }
                    }
                )
                session.add(audiobook)
                session.flush()

                log.info("imported audiobook: %s by %s (asin: %s)", book["title"], book["author"], book["asin"])
                imported += 1

                evt = HistoryEvent(
                    kind="audible_import",
                    media_kind="audiobook",
                    title=book["title"],
                    message=f"Imported from Audible (author: {book['author']})",
                    meta={
                        "asin": book["asin"],
                        "author": book["author"],
                        "narrator": book.get("narrator"),
                        "series": book.get("series")
                    }
                )
                session.add(evt)

            except Exception as exc:
                log.error("failed to import %s: %s", book.get("title", "unknown"), exc)
                match_failed += 1
                evt = HistoryEvent(
                    kind="audible_match_failed",
                    media_kind="audiobook",
                    title=book.get("title", "unknown"),
                    message=f"Import failed: {str(exc)[:200]}",
                    meta={"asin": book.get("asin"), "error": str(exc)[:100]}
                )
                session.add(evt)

        session.commit()

        if imported > 0:
            SYNC_PROGRESS["progress"] = "triggering ABS scan"
            abs.trigger_scan()

        summary = {
            "imported": imported,
            "duplicate": duplicate,
            "match_failed": match_failed,
            "error": None
        }

        SYNC_PROGRESS.update(
            running=False,
            progress=f"sync complete: {imported} imported, {duplicate} duplicate, {match_failed} failed"
        )
        log.info("Audible sync complete: %s", summary)
        return summary

    except Exception as exc:
        log.exception("Audible sync failed")
        err = str(exc)
        SYNC_PROGRESS.update(running=False, error=err)
        return {"imported": 0, "duplicate": 0, "match_failed": 0, "error": err}
    finally:
        try:
            conn.close()
        except Exception:
            pass
