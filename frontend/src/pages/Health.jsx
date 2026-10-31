import React from "react";
import { useApi } from "../hooks/useApi";
import PageHeader from "../components/PageHeader";
import { Loading, ErrorState } from "../components/States";
import { IconRefresh } from "../components/icons";

function StatusDot({ status }) {
  const color = status === "ok" ? "var(--ok)" : status === "warn" ? "var(--warn)" : "var(--err)";
  return <span style={{ display: "inline-block", width: 11, height: 11, borderRadius: "50%", background: color, flexShrink: 0 }} />;
}

export default function Health() {
  const { data, loading, error, refetch } = useApi("/health");
  const checks = data?.checks || [];
  const counts = checks.reduce((acc, c) => ({ ...acc, [c.status]: (acc[c.status] || 0) + 1 }), {});

  return (
    <div>
      <PageHeader
        title="Health"
        subtitle="qBittorrent, VPN guard, indexers, Plex, disks and database"
        actions={
          <button className="btn btn-secondary btn-sm" onClick={refetch}><IconRefresh size={14} /> Recheck</button>
        }
      />

      {loading && <Loading label="Running checks…" />}
      {error && <ErrorState error={error} onRetry={refetch} />}

      {!loading && data && (
        <>
          <div className="stats-row">
            <div className="stat-card">
              <div className="stat-value" style={{ color: data.status === "ok" ? "var(--ok)" : data.status === "warn" ? "var(--warn)" : "var(--err)" }}>
                {data.status.toUpperCase()}
              </div>
              <div className="stat-label">Overall</div>
            </div>
            <div className="stat-card"><div className="stat-value" style={{ color: "var(--ok)" }}>{counts.ok || 0}</div><div className="stat-label">OK</div></div>
            <div className="stat-card"><div className="stat-value" style={{ color: "var(--warn)" }}>{counts.warn || 0}</div><div className="stat-label">Warn</div></div>
            <div className="stat-card"><div className="stat-value" style={{ color: "var(--err)" }}>{counts.error || 0}</div><div className="stat-label">Error</div></div>
          </div>

          <div className="list">
            {checks.map((c, i) => (
              <div className={"list-item" + (c.status === "error" ? " highlight" : "")} key={i}>
                <StatusDot status={c.status} />
                <div className="list-item-main">
                  <div className="list-item-title">{c.name}</div>
                  <div className="list-item-sub">{c.detail}</div>
                </div>
                <span className={"badge badge-" + (c.status === "ok" ? "ok" : c.status === "warn" ? "warn" : "error")}>{c.status}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
