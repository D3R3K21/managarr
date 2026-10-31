import React, { useState } from "react";
import api from "../api/client";
import { useToast } from "../hooks/useToast";
import { formatBytes } from "../utils/format";
import { Loading, ErrorState, EmptyState } from "./States";
import { IconSearch, IconDownload } from "./icons";

// Interactive indexer search + grab panel. Reused by all three detail pages.
// props: mediaKind, defaultQuery, author?, grabContext (extra body for POST /grabs
//   e.g. {item_id, title, year, season, author, series})
//   upgradeMovieId? — when set, grabbing a release UPGRADES that owned movie (downloads
//     into its folder and replaces the old file on import) instead of a plain grab.
//   onGrabbed? — called after a successful grab/upgrade (e.g. to refetch the page).
export default function ReleaseSearch({ mediaKind, defaultQuery = "", author = "", grabContext = {},
                                        upgradeMovieId = null, onGrabbed }) {
  const t = useToast();
  const [query, setQuery] = useState(defaultQuery);
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [grabbing, setGrabbing] = useState(null);

  async function search(e) {
    if (e) e.preventDefault();
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const itemId = grabContext.item_id ?? upgradeMovieId;
      const data = await api.get("/indexers/search", {
        media_kind: mediaKind,
        q: query,
        author,
        ...(itemId != null ? { item_id: itemId } : {}),
      });
      setResults(Array.isArray(data) ? data : []);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  async function grab(release) {
    setGrabbing(release.download_url || release.title);
    try {
      if (upgradeMovieId) {
        // Manual pick = the user's explicit choice -> force, and replace on import.
        const r = await api.post(`/library/movies/${upgradeMovieId}/upgrade`, { release, force: true });
        if (r.status === "grabbed") t.success(`Upgrading to: ${release.title}`);
        else t.error(`Upgrade not started: ${r.reason || r.status}`);
      } else {
        await api.post("/grabs", { release, media_kind: mediaKind, source: "manual", ...grabContext });
        t.success(`Grabbed: ${release.title}`);
      }
      onGrabbed?.();
    } catch (err) {
      t.error(`${upgradeMovieId ? "Upgrade" : "Grab"} failed: ${err.message}`);
    } finally {
      setGrabbing(null);
    }
  }
  const actionLabel = upgradeMovieId ? "Upgrade" : "Grab";

  return (
    <div>
      <form className="row mb-3" onSubmit={search}>
        <input
          className="input search-bar"
          placeholder="Search releases…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button className="btn btn-primary btn-sm" type="submit" disabled={loading}>
          <IconSearch size={14} /> Search
        </button>
      </form>

      {loading && <Loading label="Searching indexers…" />}
      {error && <ErrorState error={error} onRetry={search} />}
      {!loading && !error && results && results.length === 0 && (
        <EmptyState icon="🔍" title="No releases found" hint="Try a different query." />
      )}

      {!loading && results && results.length > 0 && (
        <div className="table-wrap">
          {results.map((r, i) => (
            <div className="release-row" key={(r.infohash || r.download_url || "") + i}
                 style={r.owned ? { opacity: 0.55 } : undefined}>
              <div className="release-title" title={r.title}>{r.title}</div>
              <div className="release-meta">{r.indexer}</div>
              <div className="release-meta">{formatBytes(r.size_bytes)}</div>
              <div className="release-meta" title="seeders / leechers">
                <span style={{ color: "var(--ok)" }}>▲{r.seeders ?? 0}</span>{" "}
                <span style={{ color: "var(--text-faint)" }}>▼{r.leechers ?? 0}</span>
              </div>
              <button
                className="btn btn-success btn-xs"
                onClick={() => grab(r)}
                disabled={r.owned || grabbing === (r.download_url || r.title)}
                title={r.owned ? "This is the release you already have" : undefined}
              >
                <IconDownload size={12} />
                {r.owned ? "Owned" : grabbing === (r.download_url || r.title) ? "…" : actionLabel}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
