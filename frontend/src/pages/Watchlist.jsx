import React, { useState } from "react";
import api from "../api/client";
import { useApi } from "../hooks/useApi";
import { useToast } from "../hooks/useToast";
import PageHeader from "../components/PageHeader";
import { Loading, ErrorState, EmptyState } from "../components/States";
import { IconRefresh } from "../components/icons";

export default function Watchlist() {
  const t = useToast();
  const list = useApi("/watchlist");
  const status = useApi("/watchlist/status");
  const [syncing, setSyncing] = useState(false);

  async function sync() {
    setSyncing(true);
    try {
      const r = await api.post("/watchlist/sync");
      t.success(r.status || `Synced ${r.items} items, ${r.grabbed} grabbed`);
      list.refetch();
      status.refetch();
    } catch (e) {
      t.error(e.message);
    } finally {
      setSyncing(false);
    }
  }

  const s = status.data;
  const items = list.data?.items || [];

  return (
    <div>
      <PageHeader
        title="Watchlist"
        subtitle="Synced Plex watchlist across managed users"
        actions={
          <button className="btn btn-primary btn-sm" onClick={sync} disabled={syncing}>
            <IconRefresh size={14} /> {syncing ? "Syncing…" : "Sync now"}
          </button>
        }
      />

      <div className="card mb-3">
        <div className="row-wrap" style={{ gap: 18 }}>
          <div>
            <div className="faint" style={{ fontSize: 11, textTransform: "uppercase" }}>Plex</div>
            <div>{s?.configured ? <span className="badge badge-ok">configured</span> : <span className="badge badge-warn">no token</span>}</div>
          </div>
          <div>
            <div className="faint" style={{ fontSize: 11, textTransform: "uppercase" }}>Items</div>
            <div>{s?.item_count ?? "—"}</div>
          </div>
          <div>
            <div className="faint" style={{ fontSize: 11, textTransform: "uppercase" }}>Users</div>
            <div>{(s?.users || []).join(", ") || "—"}</div>
          </div>
          <div style={{ flex: 1 }}>
            <div className="faint" style={{ fontSize: 11, textTransform: "uppercase" }}>Last status</div>
            <div className="muted">{s?.last_status || list.data?.status || "never run"}</div>
          </div>
        </div>
      </div>

      {list.loading && <Loading />}
      {list.error && <ErrorState error={list.error} onRetry={list.refetch} />}
      {!list.loading && items.length === 0 && (
        <EmptyState icon="🔖" title="Watchlist empty" hint="Run a sync to pull from plex.tv." />
      )}

      {items.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr><th>Title</th><th>Type</th><th>Year</th><th>Status</th><th>IDs</th></tr>
            </thead>
            <tbody>
              {items.map((it, i) => (
                <tr key={(it.ratingKey || it.title || "") + i}>
                  <td style={{ fontWeight: 600 }}>{it.title}</td>
                  <td><span className="badge badge-muted">{it.type}</span></td>
                  <td>{it.year || "—"}</td>
                  <td>{it.owned ? <span className="badge badge-ok">owned</span> : <span className="badge badge-info">wanted</span>}</td>
                  <td className="faint" style={{ fontSize: 12 }}>
                    {Object.entries(it.guids || {}).map(([k, v]) => `${k}:${v}`).join(" ") || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
