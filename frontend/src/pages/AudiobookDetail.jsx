import React, { useState, useEffect, useRef, useCallback } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import api from "../api/client";
import { useApi } from "../hooks/useApi";
import { useToast } from "../hooks/useToast";
import Switch from "../components/Switch";
import KebabMenu from "../components/KebabMenu";
import FixMatchModal from "../components/FixMatchModal";
import DeleteDialog from "../components/DeleteDialog";
import ReleaseSearch from "../components/ReleaseSearch";
import { Loading, ErrorState } from "../components/States";
import { formatDuration, providers } from "../utils/format";
import { IconRefresh, IconArrowLeft } from "../components/icons";

// Split a genres array into trimmed, comma-exploded chips. A single entry may
// itself be comma-joined ("Science Fiction & Fantasy, Fantasy").
function genreChips(genres) {
  if (!Array.isArray(genres)) return [];
  const out = [];
  const seen = new Set();
  for (const g of genres) {
    if (typeof g !== "string") continue;
    for (const part of g.split(",")) {
      const v = part.trim();
      if (v && !seen.has(v.toLowerCase())) {
        seen.add(v.toLowerCase());
        out.push(v);
      }
    }
  }
  return out;
}

export default function AudiobookDetail() {
  const { id } = useParams();
  const t = useToast();
  const nav = useNavigate();
  const { data: book, loading, error, refetch, setData } = useApi(`/library/audiobooks/${id}`);
  const [refreshing, setRefreshing] = useState(false);
  const [fixOpen, setFixOpen] = useState(false);
  const [delOpen, setDelOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [showFullOverview, setShowFullOverview] = useState(false);
  const [conv, setConv] = useState(null);   // active/last m4b job for this book
  const pollRef = useRef(null);

  const pollJobs = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const jobs = await api.get("/library/audiobooks/m4b/jobs");
        const mine = (jobs || []).find((j) => String(j.audiobook_id) === String(id));
        if (!mine) return;
        setConv(mine);
        if (mine.status === "done" || mine.status === "error") {
          clearInterval(pollRef.current); pollRef.current = null;
          if (mine.status === "done") { t.success("Converted to m4b"); refetch(); }
          else t.error(`Conversion failed: ${mine.message || ""}`);
        }
      } catch { /* keep polling */ }
    }, 2000);
  }, [id, t, refetch]);

  const convertM4b = useCallback(async () => {
    try {
      const r = await api.post(`/library/audiobooks/${id}/convert-m4b`);
      setConv(r.job || { status: "queued", progress: 0 });
      t.info(r.started === false ? "Conversion already running" : "Converting to m4b…");
      pollJobs();
    } catch (e) {
      t.error(e.message);
    }
  }, [id, t, pollJobs]);

  // Resume progress if a conversion is already running when the page loads.
  useEffect(() => {
    let cancelled = false;
    api.get("/library/audiobooks/m4b/jobs").then((jobs) => {
      if (cancelled) return;
      const mine = (jobs || []).find((j) => String(j.audiobook_id) === String(id)
        && (j.status === "queued" || j.status === "running"));
      if (mine) { setConv(mine); pollJobs(); }
    }).catch(() => {});
    return () => { cancelled = true; if (pollRef.current) clearInterval(pollRef.current); };
  }, [id, pollJobs]);

  if (loading) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={refetch} />;
  if (!book) return null;

  const meta = book.meta || {};
  const prov = providers(book);
  const genres = genreChips(meta.genres);
  const publisher = meta.publisher || null;
  const language = meta.language || null;
  const series = meta.scanned_series || null;

  const googleVolume = prov.google_books?.volume_id || prov.google_books?.id || null;
  const olWork = prov.openlibrary?.work_id || null;

  const overview = book.overview || "";
  const overviewIsLong = overview.length > 600;

  // key/value pairs for the Details section; nulls are filtered out.
  const details = [
    ["Author", book.author_name],
    ["Narrator", book.narrator],
    ["Series", series],
    ["Publisher", publisher],
    ["Published", book.publish_year],
    ["Language", language],
    ["Duration", book.duration_seconds ? formatDuration(book.duration_seconds) : null],
    ["ISBN", book.isbn],
    ["ASIN", book.asin],
  ].filter(([, v]) => v != null && v !== "");

  const metaRow = [
    book.publish_year,
    book.duration_seconds ? formatDuration(book.duration_seconds) : null,
    language,
    publisher,
  ].filter(Boolean);

  async function toggleMonitored() {
    try {
      await api.patch(`/library/audiobooks/${id}/monitored`, { monitored: !book.monitored });
      setData({ ...book, monitored: !book.monitored });
    } catch (e) {
      t.error(e.message);
    }
  }

  async function doDelete(deleteFiles) {
    setDeleting(true);
    try {
      await api.del(`/library/audiobooks/${id}`, { delete_files: deleteFiles });
      t.success(deleteFiles ? "Deleted (files removed)" : "Removed from library");
      nav("/audiobooks");
    } catch (e) {
      t.error(e.message);
      setDeleting(false);
    }
  }

  async function refreshMeta() {
    setRefreshing(true);
    try {
      await api.post(`/library/audiobooks/${id}/refresh-metadata`);
      t.success("Metadata refreshed");
      refetch();
    } catch (e) {
      t.error(e.message);
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <div>
      <Link to="/audiobooks" className="btn btn-ghost btn-sm mb-3"><IconArrowLeft size={14} /> Audiobooks</Link>
      <div className="detail-layout">
        <div>
          {book.cover_url ? (
            <img className="detail-poster" src={book.cover_url} alt={book.title} />
          ) : (
            <div className="detail-poster placeholder">📚</div>
          )}
        </div>
        <div>
          <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
            <div>
              <div className="detail-title">
                {book.title}
                {!book.has_m4b && <span className="badge badge-warn" style={{ marginLeft: 10, verticalAlign: "middle", fontSize: 11 }}>no m4b</span>}
              </div>
              {book.subtitle && (
                <div className="muted" style={{ marginTop: 4, fontSize: 15 }}>{book.subtitle}</div>
              )}
              {series && (
                <div style={{ marginTop: 8, fontSize: 14, fontWeight: 600 }}>
                  {book.collection_id ? (
                    <Link to={`/collections/${book.collection_id}`} style={{ color: "var(--accent-soft)" }}>
                      📚 {series}
                    </Link>
                  ) : (
                    <span style={{ color: "var(--accent-soft)" }}>📚 {series}</span>
                  )}
                </div>
              )}
              {book.author_name && (
                <div className="muted" style={{ marginTop: 8, fontSize: 14 }}>by {book.author_name}</div>
              )}
              {book.narrator && (
                <div className="faint" style={{ marginTop: 2, fontSize: 13 }}>Narrated by {book.narrator}</div>
              )}
              <div className="detail-meta">
                {metaRow.join(" · ")}
              </div>
            </div>
            <div className="row gap-sm">
              <span className="faint" style={{ fontSize: 12 }}>Monitored</span>
              <Switch checked={book.monitored} onChange={toggleMonitored} />
              <button className="btn btn-secondary btn-sm" onClick={refreshMeta} disabled={refreshing}>
                <IconRefresh size={14} /> {refreshing ? "…" : "Refresh"}
              </button>
              {!book.has_m4b && (() => {
                const busy = conv && (conv.status === "queued" || conv.status === "running");
                return (
                  <button className="btn btn-primary btn-sm" onClick={convertM4b} disabled={busy}
                    title="Combine the MP3 files into one chapterized .m4b (originals kept)">
                    {busy ? `Converting… ${conv.progress || 0}%` : "Convert to m4b"}
                  </button>
                );
              })()}
              <KebabMenu items={[
                { label: "Fix match", onClick: () => setFixOpen(true) },
                { label: "Delete from library", onClick: () => setDelOpen(true), danger: true },
              ]} />
            </div>
          </div>

          {overview && (
            <>
              <div
                className={"detail-overview rich-html" + (overviewIsLong && !showFullOverview ? " clamped" : "")}
                dangerouslySetInnerHTML={{ __html: overview }}
              />
              {overviewIsLong && (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  style={{ display: "block", marginTop: 4, padding: "2px 8px" }}
                  onClick={() => setShowFullOverview((v) => !v)}
                >
                  {showFullOverview ? "Show less" : "Show more"}
                </button>
              )}
            </>
          )}

          {genres.length > 0 && (
            <div className="detail-section">
              <div className="section-title">Genres</div>
              <div className="row-wrap">
                {genres.map((g) => (
                  <span key={g} className="badge">{g}</span>
                ))}
              </div>
            </div>
          )}

          {details.length > 0 && (
            <div className="detail-section">
              <div className="section-title">Details</div>
              <dl className="kv-grid">
                {details.map(([k, v]) => (
                  <React.Fragment key={k}>
                    <dt>{k}</dt>
                    <dd>{v}</dd>
                  </React.Fragment>
                ))}
              </dl>
            </div>
          )}

          {(book.asin || book.isbn || googleVolume || olWork) && (
            <div className="detail-section">
              <div className="section-title">Identifiers</div>
              <div className="row-wrap">
                {book.asin && (
                  <a
                    className="badge badge-info"
                    href={`https://www.audible.com/pd/${book.asin}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    ASIN {book.asin}
                  </a>
                )}
                {book.isbn && <span className="badge badge-muted">ISBN {book.isbn}</span>}
                {googleVolume && (
                  <a
                    className="badge badge-muted"
                    href={`https://books.google.com/books?id=${googleVolume}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Google Books
                  </a>
                )}
                {olWork && (
                  <a
                    className="badge badge-muted"
                    href={`https://openlibrary.org${olWork}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    OpenLibrary
                  </a>
                )}
              </div>
            </div>
          )}

          <div className="detail-section">
            <div className="section-title">Search releases</div>
            <ReleaseSearch
              mediaKind="audiobook"
              defaultQuery={book.title}
              author={book.author_name || ""}
              grabContext={{
                item_id: book.id,
                title: book.title,
                author: book.author_name || "",
              }}
            />
          </div>

          {book.folder_path && (
            <div className="faint" style={{ fontSize: 11, fontFamily: "monospace", margin: "2px 0 12px", wordBreak: "break-all" }}>
              {book.folder_path}
            </div>
          )}
        </div>
      </div>
      <FixMatchModal
        open={fixOpen}
        onClose={() => setFixOpen(false)}
        kind="audiobook"
        kindPath="audiobooks"
        id={id}
        initialQuery={book.author_name ? `${book.title} ${book.author_name}` : book.title}
        onApplied={refetch}
      />
      <DeleteDialog
        open={delOpen}
        title="Delete audiobook"
        message={`Remove “${book.title}” from the library?`}
        fileHint="“Keep files” leaves the book on disk (a rescan re-adds it). “Delete files” permanently removes its folder and any in-progress download."
        busy={deleting}
        onClose={() => setDelOpen(false)}
        onConfirm={doDelete}
      />
    </div>
  );
}
