import React from "react";
import { Link } from "react-router-dom";
import { useApi } from "../hooks/useApi";
import PageHeader from "../components/PageHeader";
import { Loading } from "../components/States";
import { timeAgo } from "../utils/format";
import { IconFilm, IconTv, IconBook, IconRefresh } from "../components/icons";

function StatusDot({ status }) {
  const color = status === "ok" ? "var(--ok)" : status === "warn" ? "var(--warn)" : "var(--err)";
  return <span style={{ display: "inline-block", width: 9, height: 9, borderRadius: "50%", background: color }} />;
}

export default function Dashboard() {
  const movies = useApi("/library/movies");
  const tv = useApi("/library/tv");
  const audiobooks = useApi("/library/audiobooks");
  const missing = useApi("/collections/missing");
  const queue = useApi("/grabs/queue");
  const discoverMovies = useApi("/discover/movies");
  const grabs = useApi("/grabs", { limit: 12 });
  const health = useApi("/health");

  const libraryCount =
    (movies.data?.length || 0) + (tv.data?.length || 0) + (audiobooks.data?.length || 0);

  const events = grabs.data?.events || [];
  const recentGrabs = grabs.data?.grabs || [];
  const healthChecks = health.data?.checks || [];
  const candidateCount = discoverMovies.data?.length;

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle="Library overview, recent activity and system health"
        actions={
          <button className="btn btn-secondary btn-sm" onClick={() => {
            movies.refetch(); tv.refetch(); audiobooks.refetch();
            missing.refetch(); queue.refetch(); grabs.refetch(); health.refetch();
          }}>
            <IconRefresh size={14} /> Refresh
          </button>
        }
      />

      <div className="stats-row">
        <Link to="/movies" className="stat-card">
          <div className="stat-value"><IconFilm size={20} /> {movies.loading ? "…" : (movies.data?.length ?? 0)}</div>
          <div className="stat-label">Movies</div>
        </Link>
        <Link to="/tv" className="stat-card">
          <div className="stat-value"><IconTv size={20} /> {tv.loading ? "…" : (tv.data?.length ?? 0)}</div>
          <div className="stat-label">TV Series</div>
        </Link>
        <Link to="/audiobooks" className="stat-card">
          <div className="stat-value"><IconBook size={20} /> {audiobooks.loading ? "…" : (audiobooks.data?.length ?? 0)}</div>
          <div className="stat-label">Audiobooks</div>
        </Link>
        <div className="stat-card">
          <div className="stat-value">{libraryCount}</div>
          <div className="stat-label">Total Library</div>
        </div>
        <Link to="/collections" className="stat-card">
          <div className="stat-value accent">{missing.loading ? "…" : (missing.data?.length ?? 0)}</div>
          <div className="stat-label">Missing</div>
        </Link>
        <Link to="/activity" className="stat-card">
          <div className="stat-value accent">{queue.loading ? "…" : (queue.data?.length ?? 0)}</div>
          <div className="stat-label">In Queue</div>
        </Link>
        <Link to="/discover" className="stat-card">
          <div className="stat-value">{discoverMovies.loading ? "…" : (candidateCount ?? "—")}</div>
          <div className="stat-label">Discover Candidates</div>
        </Link>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="card-title">Recent Activity</div>
          {grabs.loading && <Loading label="Loading activity…" />}
          {!grabs.loading && events.length === 0 && recentGrabs.length === 0 && (
            <div className="muted" style={{ padding: 12 }}>No activity yet.</div>
          )}
          <div className="list">
            {events.map((e) => (
              <div className="list-item" key={"ev" + e.id}>
                <span className={"badge " + badgeForKind(e.kind)}>{e.kind}</span>
                <div className="list-item-main">
                  <div className="list-item-title">{e.title || e.message || "—"}</div>
                  {e.message && e.title && <div className="list-item-sub">{e.message}</div>}
                </div>
                <span className="faint" style={{ fontSize: 12 }}>{timeAgo(e.created_at)}</span>
              </div>
            ))}
            {events.length === 0 && recentGrabs.map((g) => (
              <div className="list-item" key={"gr" + g.id}>
                <span className="badge badge-accent">{g.state}</span>
                <div className="list-item-main">
                  <div className="list-item-title">{g.title}</div>
                  <div className="list-item-sub">{g.media_kind} · {g.indexer}</div>
                </div>
                <span className="faint" style={{ fontSize: 12 }}>{timeAgo(g.created_at)}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="card">
          <div className="card-title row" style={{ justifyContent: "space-between" }}>
            <span>System Health</span>
            {health.data && (
              <span className={"badge badge-" + (health.data.status === "ok" ? "ok" : health.data.status === "warn" ? "warn" : "error")}>
                {health.data.status}
              </span>
            )}
          </div>
          {health.loading && <Loading label="Checking…" />}
          <div className="list">
            {healthChecks.map((c, i) => (
              <div className="list-item" key={i}>
                <StatusDot status={c.status} />
                <div className="list-item-main">
                  <div className="list-item-title" style={{ fontWeight: 500 }}>{c.name}</div>
                  <div className="list-item-sub">{c.detail}</div>
                </div>
              </div>
            ))}
          </div>
          <Link to="/health" className="btn btn-ghost btn-sm mt-3">View all checks</Link>
        </div>
      </div>
    </div>
  );
}

function badgeForKind(kind) {
  if (["imported", "transcoded", "grabbed"].includes(kind)) return "badge-ok";
  if (["failed", "error"].includes(kind)) return "badge-error";
  if (kind === "scan") return "badge-info";
  return "badge-muted";
}
