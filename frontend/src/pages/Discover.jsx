import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import api from "../api/client";
import { useApi } from "../hooks/useApi";
import { usePersistedState } from "../hooks/usePersistedState";
import { useToast } from "../hooks/useToast";
import PageHeader from "../components/PageHeader";
import MediaCard from "../components/MediaCard";
import MediaInfoModal from "../components/MediaInfoModal";
import { Loading, ErrorState, EmptyState } from "../components/States";
import { IconDownload, IconPlus } from "../components/icons";

const TMDB_IMG = "https://image.tmdb.org/t/p/w342";
const THIS_YEAR = 2026;

function tmdbPoster(item) {
  if (item.poster_url) return item.poster_url;
  if (item.poster_path) return TMDB_IMG + item.poster_path;
  return null;
}

export default function Discover() {
  const [tab, setTab] = usePersistedState("discover:tab", "movies"); // movies | tv | audiobooks
  const [year, setYear] = usePersistedState("discover:year", "");    // "" = trending
  const years = useMemo(() => {
    const ys = [];
    for (let y = THIS_YEAR; y >= 1950; y--) ys.push(y);
    return ys;
  }, []);
  const showYear = tab === "movies" || tab === "tv";

  return (
    <div>
      <PageHeader title="Discover" subtitle="Trending, or browse a whole year — minus what you already own" />
      <div className="filter-row">
        {[["movies", "Movies"], ["tv", "TV"], ["audiobooks", "Audiobooks"]].map(([k, label]) => (
          <button key={k} className={"chip" + (tab === k ? " active" : "")} onClick={() => setTab(k)}>
            {label}
          </button>
        ))}
        {showYear && (
          <select className="input" value={year} onChange={(e) => setYear(e.target.value)}
            style={{ maxWidth: 160, marginLeft: 12 }} title="Browse every title from a year (via TMDB)">
            <option value="">Trending now</option>
            {years.map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
        )}
      </div>

      {tab === "movies" && (year
        ? <DiscoverYearGrid kind="movie" year={year} />
        : <DiscoverGrid path="/discover/movies" kind="movie" />)}
      {tab === "tv" && (year
        ? <DiscoverYearGrid kind="tv" year={year} />
        : <DiscoverGrid path="/discover/tv" kind="tv" />)}
      {tab === "audiobooks" && <DiscoverAudiobooks />}
    </div>
  );
}

// ── deep year browse (TMDB /discover, paginated, owned filtered) ───────────────
function DiscoverYearGrid({ kind, year }) {
  const t = useToast();
  const genresApi = useApi("/discover/genres", { kind });
  const [genre, setGenre] = usePersistedState("discover:genre:" + kind, "");
  const [english, setEnglish] = usePersistedState("discover:english", true);
  const [items, setItems] = useState([]);
  const [page, setPage] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [totalResults, setTotalResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(null);
  const [adding, setAdding] = useState({});
  const sentinelRef = useRef(null);

  const load = useCallback(async (p, reset) => {
    setLoading(true);
    try {
      const params = { year, page: p, kind, english };
      if (genre) params.genre = genre;
      const r = await api.get("/discover/by-year", params);
      setItems((prev) => (reset ? (r.results || []) : [...prev, ...(r.results || [])]));
      setTotalPages(r.total_pages || 1);
      setTotalResults(r.total_results ?? null);
      setPage(r.page || p);
    } catch (e) {
      t.error(e.message);
    } finally {
      setLoading(false);
    }
  }, [year, kind, genre, english, t]);

  useEffect(() => { setItems([]); setPage(0); load(1, true); }, [load]);

  // Infinite scroll — fetch the next page when the sentinel approaches the viewport.
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || loading || page >= totalPages) return undefined;
    const obs = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting) load(page + 1, false);
    }, { rootMargin: "800px" });
    obs.observe(el);
    return () => obs.disconnect();
  }, [loading, page, totalPages, load]);

  async function openInfo(item) {
    setSelected(item);
    try {
      const { trailers } = await api.get("/discover/trailers", { tmdb_id: item.id, kind });
      if (trailers?.length) setSelected((cur) => (cur && cur.id === item.id ? { ...cur, trailers } : cur));
    } catch { /* best-effort */ }
  }

  async function add(item, download) {
    const key = item.id;
    setAdding((a) => ({ ...a, [key]: download ? "dl" : "lib" }));
    try {
      const yr = Number((item.release_date || item.first_air_date || "").slice(0, 4)) || Number(year) || undefined;
      await api.post("/search/add", {
        kind, provider: "tmdb", provider_id: String(item.id),
        title: item.title || item.name, year: yr, download,
      });
      t.success(download ? `Added + downloading: ${item.title || item.name}` : `Added: ${item.title || item.name}`);
      // Disable the card in place (don't drop/reorder) so scrolling isn't disturbed.
      setAdding((a) => ({ ...a, [key]: download ? "done-dl" : "done-lib" }));
      setSelected((cur) => (cur && cur.id === item.id ? null : cur));
    } catch (e) {
      t.error(e.message);
      setAdding((a) => { const n = { ...a }; delete n[key]; return n; });
    }
  }

  const genreList = genresApi.data || [];
  return (
    <>
      <div className="filter-row" style={{ marginBottom: 10 }}>
        <select className="input" value={genre} onChange={(e) => setGenre(e.target.value)} style={{ maxWidth: 190 }}>
          <option value="">All genres</option>
          {genreList.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
        </select>
        <label className="faint" style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, cursor: "pointer" }}>
          <input type="checkbox" checked={english} onChange={(e) => setEnglish(e.target.checked)} />
          English only
        </label>
        <span className="faint" style={{ marginLeft: "auto", fontSize: 12 }}>
          {totalResults != null
            ? `${totalResults.toLocaleString()} ${kind === "tv" ? "shows" : "movies"} from ${year} not in your library`
            : `${kind === "tv" ? "Shows" : "Movies"} from ${year}`}
        </span>
      </div>

      {loading && items.length === 0 ? (
        <Loading />
      ) : items.length === 0 ? (
        <EmptyState icon="🎬" title={`Nothing new from ${year}`} hint="Try another genre/year, or turn off 'English only'." />
      ) : (
        <>
          <div className="media-grid">
            {items.map((item) => {
              const title = item.title || item.name || "Untitled";
              const yr = (item.release_date || item.first_air_date || "").slice(0, 4);
              const busy = adding[item.id];
              const done = busy === "done-dl" || busy === "done-lib";
              return (
                <div key={item.id} style={{ position: "relative", opacity: done ? 0.45 : 1 }}>
                  <div className="card-clickable" onClick={() => done ? null : openInfo(item)} role="button" tabIndex={0}>
                    <MediaCard poster={tmdbPoster(item)} title={title} sub={yr || null} fallback={kind === "tv" ? "📺" : "🎬"} />
                  </div>
                  <button className="btn btn-success btn-xs" style={{ position: "absolute", bottom: 10, right: 10 }}
                    disabled={!!busy} onClick={() => add(item, true)}>
                    <IconDownload size={12} /> {busy === "dl" ? "…" : done ? "Added" : "Grab"}
                  </button>
                </div>
              );
            })}
          </div>
          {/* Infinite scroll: load the next page when this sentinel nears the viewport */}
          {page < totalPages && (
            <>
              <div ref={sentinelRef} style={{ height: 1 }} />
              <div className="row" style={{ justifyContent: "center", marginTop: 16 }}>
                {loading
                  ? <span className="faint" style={{ fontSize: 13 }}>Loading more…</span>
                  : <button className="btn btn-ghost btn-sm" onClick={() => load(page + 1, false)}>Load more</button>}
              </div>
            </>
          )}
        </>
      )}

      <MediaInfoModal
        open={!!selected}
        item={selected}
        onClose={() => setSelected(null)}
        action={selected && (
          <div className="row gap-sm">
            <button className="btn btn-ghost btn-sm" disabled={!!adding[selected.id]} onClick={() => add(selected, false)}>
              <IconPlus size={14} /> Add
            </button>
            <button className="btn btn-success btn-sm" disabled={!!adding[selected.id]} onClick={() => add(selected, true)}>
              <IconDownload size={14} /> Add + Download
            </button>
          </div>
        )}
      />
    </>
  );
}

// ── trending grid (unchanged) ──────────────────────────────────────────────────
function DiscoverGrid({ path, kind }) {
  const t = useToast();
  const { data, loading, error, refetch } = useApi(path);
  const [grabbing, setGrabbing] = useState(null);
  const [grabbed, setGrabbed] = useState({});
  const [selected, setSelected] = useState(null);

  async function openInfo(item) {
    setSelected(item);
    try {
      const { trailers } = await api.get("/discover/trailers", { tmdb_id: item.id, kind });
      if (trailers?.length) setSelected((cur) => (cur && cur.id === item.id ? { ...cur, trailers } : cur));
    } catch { /* trailers are best-effort */ }
  }

  async function grab(item) {
    setGrabbing(item.id);
    try {
      const title = item.title || item.name || "";
      const year = (item.release_date || item.first_air_date || "").slice(0, 4);
      // Add the discovered title to the library (monitored) and let the backend
      // search indexers + download the best release. Posting a fake release to
      // /grabs can't work — it has no real indexer/download_url.
      await api.post("/search/add", {
        kind,
        provider: "tmdb",
        provider_id: item.id,
        title,
        year: year ? Number(year) : undefined,
        download: true,
      });
      t.success(`Added & downloading: ${title}`);
      setGrabbed((g) => ({ ...g, [item.id]: true }));   // keep the card marked, no reload
    } catch (e) {
      t.error(`Couldn't add ${title}: ${e.message}`);
    } finally {
      setGrabbing(null);
    }
  }

  if (loading) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={refetch} />;
  if (!data || data.length === 0) return <EmptyState icon="🧭" title="Nothing to discover" hint="Either all caught up, or TMDB is unconfigured." />;

  return (
    <>
      <div className="media-grid">
        {data.map((item) => {
          const title = item.title || item.name || "Untitled";
          const year = (item.release_date || item.first_air_date || "").slice(0, 4);
          return (
            <div key={item.id} style={{ position: "relative", opacity: grabbed[item.id] ? 0.45 : 1 }}>
              <div className="card-clickable" onClick={() => openInfo(item)} role="button" tabIndex={0}>
                <MediaCard poster={tmdbPoster(item)} title={title} sub={year || null} fallback={kind === "tv" ? "📺" : "🎬"} />
              </div>
              <button
                className="btn btn-success btn-xs"
                style={{ position: "absolute", bottom: 10, right: 10 }}
                onClick={() => grab(item)}
                disabled={grabbing === item.id || grabbed[item.id]}
              >
                <IconDownload size={12} /> {grabbing === item.id ? "…" : grabbed[item.id] ? "Grabbed" : "Grab"}
              </button>
            </div>
          );
        })}
      </div>
      <MediaInfoModal
        open={!!selected}
        item={selected}
        onClose={() => setSelected(null)}
        action={
          selected && (
            <button
              className="btn btn-success btn-sm"
              onClick={() => { grab(selected); setSelected(null); }}
              disabled={grabbing === selected.id}
            >
              <IconDownload size={14} /> {grabbing === selected.id ? "…" : "Grab"}
            </button>
          )
        }
      />
    </>
  );
}

function DiscoverAudiobooks() {
  const t = useToast();
  const { data, loading, error, refetch } = useApi("/discover/audiobooks");
  const [grabbing, setGrabbing] = useState(null);
  const [grabbed, setGrabbed] = useState({});
  const [selected, setSelected] = useState(null);

  async function grab(author, item) {
    const k = item.asin || item.title;
    setGrabbing(k);
    try {
      await api.post("/search/add", {
        kind: "audiobook",
        provider: "audible",
        provider_id: item.asin,
        title: item.title,
        author,
        download: true,
      });
      t.success(`Added & downloading: ${item.title}`);
      setGrabbed((g) => ({ ...g, [k]: true }));
    } catch (e) {
      t.error(`Pick a release on the book page: ${e.message}`);
    } finally {
      setGrabbing(null);
    }
  }

  if (loading) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={refetch} />;
  if (!data || data.length === 0) return <EmptyState icon="📚" title="No new audiobooks" hint="Monitored authors have no missing catalog titles." />;

  return (
    <div className="col">
      {data.map((group) => (
        <div className="card" key={group.author}>
          <div className="card-title">{group.author} <span className="faint">({group.missing.length})</span></div>
          <div className="list">
            {group.missing.map((m) => (
              <div className="list-item" key={m.asin || m.title}>
                <div
                  className="list-item-main"
                  style={{ cursor: "pointer" }}
                  onClick={() => setSelected({ ...m, author: group.author })}
                  role="button"
                  tabIndex={0}
                >
                  <div className="list-item-title">{m.title}</div>
                  <div className="list-item-sub">{m.subtitle || m.series_title || ""}</div>
                </div>
                <button className="btn btn-success btn-xs" onClick={() => grab(group.author, m)}
                  disabled={grabbing === (m.asin || m.title) || grabbed[m.asin || m.title]}>
                  <IconDownload size={12} /> {grabbing === (m.asin || m.title) ? "…" : grabbed[m.asin || m.title] ? "Grabbed" : "Grab"}
                </button>
              </div>
            ))}
          </div>
        </div>
      ))}
      <MediaInfoModal
        open={!!selected}
        item={selected}
        onClose={() => setSelected(null)}
        action={
          selected && (
            <button
              className="btn btn-success btn-sm"
              onClick={() => { grab(selected.author, selected); setSelected(null); }}
              disabled={grabbing === (selected.asin || selected.title)}
            >
              <IconDownload size={14} /> {grabbing === (selected.asin || selected.title) ? "…" : "Grab"}
            </button>
          )
        }
      />
    </div>
  );
}
