import React, { useState, useMemo } from "react";
import api from "../api/client";
import { useApi } from "../hooks/useApi";
import { usePersistedState, useScrollRestore } from "../hooks/usePersistedState";
import { useToast } from "../hooks/useToast";
import PageHeader from "../components/PageHeader";
import MediaCard from "../components/MediaCard";
import Switch from "../components/Switch";
import { Loading, ErrorState, EmptyState } from "../components/States";
import { IconRefresh } from "../components/icons";

// Issue/attribute filters for the audiobook library (persisted across navigation).
const AB_FILTERS = [
  ["all", "All books"],
  ["monitored", "Monitored"],
  ["nom4b", "No m4b (MP3 etc.)"],
  ["nocover", "Missing cover"],
  ["nometa", "Missing metadata"],
  ["unmatched", "Unmatched"],
  ["issues", "Any issue"],
  ["releaseless", "Releaseless"],
];
function abMatched(m) {
  return !!(m.asin || (m.meta && m.meta.providers && m.meta.providers.audible && m.meta.providers.audible.asin));
}
function abMatchesFilter(m, f) {
  const noCover = !m.cover_url;
  const noMeta = !m.overview;
  switch (f) {
    case "monitored": return m.monitored;
    case "nom4b": return !m.has_m4b;
    case "nocover": return noCover;
    case "nometa": return noMeta;
    case "unmatched": return !abMatched(m);
    case "issues":
      return !m.has_m4b || noCover || noMeta || !abMatched(m) || (m.issues && m.issues.length > 0);
    default: return true;
  }
}

export default function Audiobooks() {
  const t = useToast();
  const { data, loading, error, refetch, setData } = useApi("/library/audiobooks");
  const [q, setQ] = usePersistedState("audiobooks:q", "");
  const [filter, setFilter] = usePersistedState("audiobooks:filter", "all");
  const [scanning, setScanning] = useState(false);
  useScrollRestore("audiobooks", !loading && !!data);

  const filtered = useMemo(() => {
    let rows = data || [];
    if (q.trim()) {
      const s = q.toLowerCase();
      rows = rows.filter(
        (m) => (m.title || "").toLowerCase().includes(s) || (m.author_name || "").toLowerCase().includes(s)
      );
    }
    // releaseless books get their own view; every other filter hides them
    return rows.filter((m) => {
      const rl = m.meta?.acq_status === "releaseless";
      if (filter === "releaseless") return rl;
      return !rl && abMatchesFilter(m, filter);
    });
  }, [data, q, filter]);

  async function toggleMonitored(m) {
    try {
      await api.patch(`/library/audiobooks/${m.id}/monitored`, { monitored: !m.monitored });
      setData((rows) => rows.map((r) => (r.id === m.id ? { ...r, monitored: !m.monitored } : r)));
    } catch (e) {
      t.error(`Failed: ${e.message}`);
    }
  }

  async function scan() {
    setScanning(true);
    try {
      await api.post("/scan/audiobooks");
      t.success("Audiobook scan started");
    } catch (e) {
      t.error(`Scan failed: ${e.message}`);
    } finally {
      setScanning(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Audiobooks"
        subtitle={data ? `${data.length} books` : null}
        actions={
          <button className="btn btn-secondary btn-sm" onClick={scan} disabled={scanning}>
            <IconRefresh size={14} /> {scanning ? "Scanning…" : "Scan"}
          </button>
        }
      />

      <div className="filter-row">
        <input className="input search-bar" placeholder="Search title or author…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="input" value={filter} onChange={(e) => setFilter(e.target.value)} style={{ maxWidth: 220 }}>
          {AB_FILTERS.map(([f, label]) => <option key={f} value={f}>{label}</option>)}
        </select>
        <span className="faint" style={{ fontSize: 12, marginLeft: "auto" }}>{filtered.length} shown</span>
      </div>

      {loading && <Loading />}
      {error && <ErrorState error={error} onRetry={refetch} />}
      {!loading && !error && filtered.length === 0 && (
        <EmptyState icon="📚" title="No audiobooks" hint="Run a scan to populate your library." />
      )}

      {!loading && filtered.length > 0 && (
        <div className="media-grid">
          {filtered.map((m) => (
            <div key={m.id} style={{ position: "relative" }}>
              <MediaCard
                to={`/audiobooks/${m.id}`}
                poster={m.cover_url}
                title={m.title}
                sub={m.author_name || null}
                fallback="📚"
                corner={m.has_m4b ? <span style={{ color: "var(--ok)" }}>m4b</span> : null}
              />
              <div style={{ position: "absolute", bottom: 10, right: 10 }}>
                <Switch checked={m.monitored} onChange={() => toggleMonitored(m)} title="Monitored" />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
