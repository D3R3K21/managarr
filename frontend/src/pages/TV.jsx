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

export default function TV() {
  const t = useToast();
  const { data, loading, error, refetch, setData } = useApi("/library/tv");
  const [q, setQ] = usePersistedState("tv:q", "");
  const [filter, setFilter] = usePersistedState("tv:filter", "all"); // all | monitored | continuing
  const [scanning, setScanning] = useState(false);
  useScrollRestore("tv", !loading && !!data);

  const releaseless = (m) => m.meta?.acq_status === "releaseless";
  const releaselessCount = useMemo(
    () => (data || []).filter(releaseless).length, [data]);

  const filtered = useMemo(() => {
    let rows = data || [];
    if (q.trim()) {
      const s = q.toLowerCase();
      rows = rows.filter((m) => (m.title || "").toLowerCase().includes(s));
    }
    if (filter === "releaseless") return rows.filter(releaseless);
    rows = rows.filter((m) => !releaseless(m));
    if (filter === "monitored") rows = rows.filter((m) => m.monitored);
    if (filter === "continuing") rows = rows.filter((m) => (m.status || "") === "continuing");
    return rows;
  }, [data, q, filter]);

  async function toggleMonitored(m) {
    try {
      await api.patch(`/library/tv/${m.id}/monitored`, { monitored: !m.monitored });
      setData((rows) => rows.map((r) => (r.id === m.id ? { ...r, monitored: !m.monitored } : r)));
    } catch (e) {
      t.error(`Failed: ${e.message}`);
    }
  }

  async function scan() {
    setScanning(true);
    try {
      await api.post("/scan/tv");
      t.success("TV scan started");
    } catch (e) {
      t.error(`Scan failed: ${e.message}`);
    } finally {
      setScanning(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="TV"
        subtitle={data ? `${data.length} series` : null}
        actions={
          <button className="btn btn-secondary btn-sm" onClick={scan} disabled={scanning}>
            <IconRefresh size={14} /> {scanning ? "Scanning…" : "Scan"}
          </button>
        }
      />

      <div className="filter-row">
        <input className="input search-bar" placeholder="Search series…" value={q} onChange={(e) => setQ(e.target.value)} />
        {["all", "monitored", "continuing"].map((f) => (
          <button key={f} className={"chip" + (filter === f ? " active" : "")} onClick={() => setFilter(f)}>
            {f}
          </button>
        ))}
        {releaselessCount > 0 && (
          <button className={"chip" + (filter === "releaseless" ? " active" : "")}
                  onClick={() => setFilter("releaseless")} title="Auto-search found no release — review and delete">
            releaseless ({releaselessCount})
          </button>
        )}
      </div>

      {loading && <Loading />}
      {error && <ErrorState error={error} onRetry={refetch} />}
      {!loading && !error && filtered.length === 0 && (
        <EmptyState icon="📺" title="No series" hint="Run a scan to populate your library." />
      )}

      {!loading && filtered.length > 0 && (
        <div className="media-grid">
          {filtered.map((m) => (
            <div key={m.id} style={{ position: "relative" }}>
              <MediaCard
                to={`/tv/${m.id}`}
                poster={m.poster_url}
                title={m.title}
                sub={[m.year, m.status].filter(Boolean).join(" · ") || null}
                fallback="📺"
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
