import React, { useState, useEffect, useRef } from "react";
import { Link, useSearchParams } from "react-router-dom";
import api from "../api/client";
import { useApi } from "../hooks/useApi";
import { usePersistedState } from "../hooks/usePersistedState";
import { useToast } from "../hooks/useToast";
import PageHeader from "../components/PageHeader";
import MediaCard from "../components/MediaCard";
import { Loading, ErrorState, EmptyState } from "../components/States";
import { IconRefresh, IconDownload, IconSearch } from "../components/icons";

// Human label for the current rebuild phase.
const PHASE_LABELS = {
  starting: "Starting…",
  migrate_radarr: "Importing franchises from Radarr",
  movies: "Grouping movie franchises",
  missing_movies: "Finding missing movies (TMDB)",
  audiobook_series: "Grouping audiobook series",
  missing: "Finding missing books (Audible)",
  done: "Done",
};

export default function Collections() {
  const [params] = useSearchParams();
  const initialTab = params.get("author") ? "authors" : "collections";
  const [tab, setTab] = useState(initialTab);

  return (
    <div>
      <PageHeader title="Collections" subtitle="Franchises, book series, and authors" />
      <div className="filter-row">
        {[["collections", "Collections"], ["authors", "By Author"]].map(([k, label]) => (
          <button key={k} className={"chip" + (tab === k ? " active" : "")} onClick={() => setTab(k)}>
            {label}
          </button>
        ))}
      </div>
      {tab === "collections" ? <CollectionsView /> : <AuthorsView initialAuthor={params.get("author") || ""} />}
    </div>
  );
}

// Client-side filters for the collections grid (persisted across navigation).
const COLL_FILTERS = [
  ["all", "All"],
  ["incomplete", "Incomplete (missing items)"],
  ["no_art", "Missing poster / overview"],
];
function matchesFilter(c, f) {
  if (f === "incomplete") return (c.missing_count || 0) > 0;
  if (f === "no_art") return !c.poster_url || !c.overview;
  return true;
}

function CollectionsView() {
  const t = useToast();
  const [kind, setKind] = usePersistedState("collections.kind", "");
  const [filter, setFilter] = usePersistedState("collections.filter", "all");
  const { data, loading, error, refetch } = useApi("/collections", kind ? { kind } : undefined);
  const [progress, setProgress] = useState(null);   // null = idle
  const [includeMissing, setIncludeMissing] = useState(false);
  const pollRef = useRef(null);

  const running = progress?.running;

  // Resume polling if a rebuild is already running (e.g. navigated back).
  useEffect(() => {
    let cancelled = false;
    api.get("/collections/rebuild/status").then((p) => {
      if (!cancelled && p?.running) { setProgress(p); poll(); }
    }).catch(() => {});
    return () => { cancelled = true; if (pollRef.current) clearInterval(pollRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function poll() {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const p = await api.get("/collections/rebuild/status");
        setProgress(p);
        if (!p?.running) {
          clearInterval(pollRef.current);
          pollRef.current = null;
          if (p?.error) t.error(`Rebuild failed: ${p.error}`);
          else t.success("Collections rebuilt");
          refetch();
        }
      } catch (e) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }, 1500);
  }

  async function rebuild() {
    try {
      const params = { include_missing: includeMissing };
      if (kind) params.kind = kind;
      const res = await api.post("/collections/rebuild", null, params);
      if (res?.started === false) t.info("A rebuild is already running");
      setProgress(res?.progress || { running: true, phase: "starting" });
      poll();
    } catch (e) {
      t.error(e.message);
    }
  }

  const pct = progress?.total ? Math.round((progress.processed / progress.total) * 100) : 0;

  return (
    <div>
      <div className="filter-row">
        {[["", "All"], ["movie", "Movie franchises"], ["audiobook", "Book series"]].map(([k, label]) => (
          <button key={k} className={"chip" + (kind === k ? " active" : "")} onClick={() => setKind(k)}>
            {label}
          </button>
        ))}
        <select className="input" value={filter} onChange={(e) => setFilter(e.target.value)}
          style={{ maxWidth: 230, marginLeft: 12 }}>
          {COLL_FILTERS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
        <label className="faint" style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
          <input type="checkbox" checked={includeMissing} onChange={(e) => setIncludeMissing(e.target.checked)} disabled={running} />
          Find missing (slower)
        </label>
        <button className="btn btn-secondary btn-sm" onClick={() => rebuild()} disabled={running}>
          <IconRefresh size={14} /> {running ? "Rebuilding…" : "Rebuild"}
        </button>
      </div>

      {progress && running && (
        <div className="card" style={{ marginBottom: 14, padding: "10px 14px" }}>
          <div className="row" style={{ justifyContent: "space-between", fontSize: 13 }}>
            <span>{PHASE_LABELS[progress.phase] || progress.phase || "Working…"}</span>
            <span className="faint">
              {progress.total ? `${progress.processed || 0} / ${progress.total}` : ""}
              {progress.built != null ? ` · ${progress.built} built` : ""}
              {progress.added != null ? ` · ${progress.added} missing` : ""}
            </span>
          </div>
          <div className="progress" style={{ marginTop: 8 }}>
            <div className="progress-fill" style={{ width: `${pct}%` }} />
          </div>
        </div>
      )}

      {loading && <Loading />}
      {error && <ErrorState error={error} onRetry={refetch} />}
      {(() => {
        const shown = (data || []).filter((c) => matchesFilter(c, filter));
        if (loading || error) return null;
        if ((data || []).length === 0)
          return <EmptyState icon="🗂" title="No collections" hint="Rebuild to detect franchises and series." />;
        if (shown.length === 0)
          return <EmptyState icon="🗂" title="Nothing matches this filter" hint="Try a different filter above." />;
        return (
          <div className="media-grid">
            {shown.map((c) => (
              <MediaCard
                key={c.id}
                to={`/collections/${c.id}`}
                poster={c.poster_url}
                title={c.name}
                sub={c.missing_count
                  ? `${c.missing_count} missing · ${c.owned_count} owned`
                  : (c.kind === "movie" ? "Franchise" : "Series")}
                fallback="🗂"
              />
            ))}
          </div>
        );
      })()}
    </div>
  );
}

function AuthorsView({ initialAuthor }) {
  const t = useToast();
  const [author, setAuthor] = useState(initialAuthor);
  const [query, setQuery] = useState(initialAuthor);
  const { data, loading, error, refetch } = useApi(author ? "/collections/by-author" : null, author ? { author } : undefined);
  const [grabbing, setGrabbing] = useState(null);

  function submit(e) {
    e.preventDefault();
    setAuthor(query.trim());
  }

  async function grabMissing(item) {
    setGrabbing(item.asin || item.title);
    try {
      // by-author missing items aren't in the library yet — add the book (monitored)
      // and let the backend search indexers + download the best release.
      await api.post("/search/add", {
        kind: "audiobook",
        provider: "audible",
        provider_id: item.asin,
        title: item.title,
        author: data.author,
        series: item.series || "",
        download: true,
      });
      t.success(`Added & downloading ${item.title}`);
    } catch (e) {
      t.error(`Use the book page to pick a release: ${e.message}`);
    } finally {
      setGrabbing(null);
    }
  }

  return (
    <div>
      <form className="row mb-3" onSubmit={submit}>
        <input className="input search-bar" placeholder="Author name…" value={query} onChange={(e) => setQuery(e.target.value)} />
        <button className="btn btn-primary btn-sm" type="submit"><IconSearch size={14} /> Load</button>
      </form>

      {!author && <EmptyState icon="✍️" title="Enter an author" hint="See owned books and missing catalog titles." />}
      {author && loading && <Loading label="Querying catalog…" />}
      {author && error && <ErrorState error={error} onRetry={refetch} />}

      {author && data && (
        <div className="grid-2">
          <div className="card">
            <div className="card-title">Owned ({(data.owned || []).length})</div>
            <div className="list">
              {(data.owned || []).map((b) => (
                <Link to={`/audiobooks/${b.id}`} className="list-item" key={b.id}>
                  <div className="list-item-main">
                    <div className="list-item-title">{b.title}</div>
                    <div className="list-item-sub">{[b.series_position && `#${b.series_position}`, b.publish_year].filter(Boolean).join(" · ")}</div>
                  </div>
                  <span className="badge badge-ok">have</span>
                </Link>
              ))}
              {(data.owned || []).length === 0 && <div className="faint">None owned.</div>}
            </div>
          </div>

          <div className="card">
            <div className="card-title">Missing ({(data.missing || []).length})</div>
            <div className="list">
              {(data.missing || []).map((m) => (
                <div className="list-item" key={m.asin || m.title}>
                  <div className="list-item-main">
                    <div className="list-item-title">{m.title}</div>
                    <div className="list-item-sub">{[m.series, m.position && `#${m.position}`, m.year].filter(Boolean).join(" · ")}</div>
                  </div>
                  <button className="btn btn-success btn-xs" onClick={() => grabMissing(m)} disabled={grabbing === (m.asin || m.title)}>
                    <IconDownload size={12} /> {grabbing === (m.asin || m.title) ? "…" : "Grab"}
                  </button>
                </div>
              ))}
              {(data.missing || []).length === 0 && <div className="faint">Nothing missing 🎉</div>}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
