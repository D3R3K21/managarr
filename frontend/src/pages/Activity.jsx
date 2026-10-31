import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { Link } from "react-router-dom";
import api from "../api/client";
import { useApi } from "../hooks/useApi";
import { usePersistedState } from "../hooks/usePersistedState";
import { useToast } from "../hooks/useToast";
import PageHeader from "../components/PageHeader";
import DeleteDialog from "../components/DeleteDialog";
import { Loading, ErrorState, EmptyState } from "../components/States";
import { formatBytes, formatEta, timeAgo, pct } from "../utils/format";
import { IconRefresh, IconTrash, IconDownload } from "../components/icons";

const tsToIso = (sec) => (sec ? new Date(sec * 1000).toISOString() : null);
const PAGE = 50;

// Incremental skip/take loader: fetches a page at a time and appends, so a list
// never loads the whole table at once. `path` returns a plain array; the backend
// honors ?offset=&limit=. Changing `params` resets to page 0.
function usePagedList(path, params) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [done, setDone] = useState(false);
  const [error, setError] = useState(null);
  const offsetRef = useRef(0);
  const pkey = JSON.stringify(params || null);

  const fetchPage = useCallback(async (reset) => {
    setLoading(true);
    const off = reset ? 0 : offsetRef.current;
    try {
      const page = await api.get(path, { ...(params || {}), offset: off, limit: PAGE });
      const arr = Array.isArray(page) ? page : [];
      offsetRef.current = off + arr.length;
      setItems((prev) => (reset ? arr : [...prev, ...arr]));
      setDone(arr.length < PAGE);
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, pkey]);

  useEffect(() => { offsetRef.current = 0; fetchPage(true); }, [fetchPage]);

  const loadMore = useCallback(() => fetchPage(false), [fetchPage]);
  const reload = useCallback(() => { offsetRef.current = 0; return fetchPage(true); }, [fetchPage]);
  return { items, loading, done, error, loadMore, reload };
}

function LoadMore({ onClick, loading }) {
  return (
    <div className="row" style={{ justifyContent: "center", marginTop: 12 }}>
      <button className="btn btn-secondary btn-sm" onClick={onClick} disabled={loading}>
        <IconRefresh size={14} /> {loading ? "Loading…" : "Load more"}
      </button>
    </div>
  );
}

export default function Activity() {
  const [tab, setTab] = usePersistedState("activity.tab", "queue"); // queue | auto | history | blocklist
  return (
    <div>
      <PageHeader title="Activity" subtitle="Downloads, auto-queue, history and blocklist" />
      <div className="filter-row">
        {[["queue", "Queue"], ["auto", "Auto-download"], ["history", "History"], ["blocklist", "Blocklist"]].map(([k, label]) => (
          <button key={k} className={"chip" + (tab === k ? " active" : "")} onClick={() => setTab(k)}>
            {label}
          </button>
        ))}
      </div>
      {tab === "queue" && <Queue />}
      {tab === "auto" && <AutoQueue />}
      {tab === "history" && <History />}
      {tab === "blocklist" && <BlocklistView />}
    </div>
  );
}

// The throttled auto-download queue: monitored items + watched collections, gated
// by the concurrency cap. Manual grabs don't appear here (they bypass the queue).
function AutoQueue() {
  const t = useToast();
  const { data, loading, error, refetch } = useApi("/grabs/auto-queue");
  const [delItem, setDelItem] = useState(null);
  const [busy, setBusy] = useState(false);

  // Live-refresh every 5s so items move queued -> active -> done as slots free.
  useEffect(() => {
    const id = setInterval(() => refetch(), 5000);
    return () => clearInterval(id);
  }, [refetch]);

  const doCancel = useCallback(async (item, deleteFiles) => {
    setBusy(true);
    try {
      await api.del(`/grabs/auto-queue/${item.id}`, { delete_files: deleteFiles });
      t.success(deleteFiles ? "Removed (files deleted)" : "Removed from queue");
      setDelItem(null);
      refetch();
    } catch (e) { t.error(e.message); }
    finally { setBusy(false); }
  }, [refetch, t]);
  // Active downloads have partial files → ask keep/delete; others just remove.
  const onTrash = useCallback((item) => {
    if (item.state === "active") setDelItem(item);
    else doCancel(item, false);
  }, [doCancel]);
  const searchAgain = useCallback(async (id) => {
    try { await api.post(`/grabs/auto-queue/${id}/retry`); t.success("Re-queued — searching again"); refetch(); }
    catch (e) { t.error(e.message); }
  }, [refetch, t]);

  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={refetch} />;
  const items = data || [];
  if (items.length === 0) {
    return <EmptyState icon="🤖" title="Auto-download queue empty"
      hint="Monitored items and watched collections fill in here, throttled by the auto-download cap." />;
  }

  return (
    <div className="list">
      {items.map((it) => {
        const canRetry = ["parked", "no_release", "failed"].includes(it.state);
        const label = it.state === "no_release" ? "searching" : it.state;
        return (
          <div className="list-item" key={it.id}>
            <span className={"badge " + autoBadge(it.state)}>{label}</span>
            <div className="list-item-main">
              <div className="list-item-title">{it.title}</div>
              <div className="list-item-sub row" style={{ gap: 14 }}>
                <span>{[it.media_kind, it.source].filter(Boolean).join(" · ")}</span>
                {it.attempts > 0 && <span className="faint">{it.attempts} attempt{it.attempts > 1 ? "s" : ""}</span>}
                {it.last_error && <span className="faint">{it.last_error}</span>}
              </div>
            </div>
            <div className="row gap-sm">
              {canRetry && (
                <button className="btn btn-secondary btn-xs" onClick={() => searchAgain(it.id)}>Search again</button>
              )}
              <button className="btn btn-danger btn-xs" onClick={() => onTrash(it)} title="Remove from auto-download queue">
                <IconTrash size={12} />
              </button>
            </div>
          </div>
        );
      })}
      <DeleteDialog
        open={!!delItem}
        title="Stop download"
        message={delItem ? `Stop downloading “${delItem.title}”?` : ""}
        fileHint="“Keep files” leaves the partial download on disk. “Delete files” removes it from qBittorrent and deletes the partial data."
        busy={busy}
        onClose={() => setDelItem(null)}
        onConfirm={(df) => doCancel(delItem, df)}
      />
    </div>
  );
}
function autoBadge(s) {
  if (s === "active") return "badge-info";
  if (s === "done") return "badge-ok";
  if (s === "failed" || s === "parked") return "badge-error";
  return "badge-muted";
}

function Queue() {
  const { data, loading, error, refetch } = useApi("/grabs/queue");
  const completed = usePagedList("/grabs/completed");
  const active = data || [];

  // Live-refresh the active queue every 4s (small, bounded set).
  useEffect(() => {
    const id = setInterval(() => refetch(), 4000);
    return () => clearInterval(id);
  }, [refetch]);

  // Refresh the Completed list only when something LEAVES the active queue (a
  // download finished) — not on every 4s tick, so it doesn't clobber loaded pages.
  const prevActive = useRef(0);
  const reloadCompleted = completed.reload;
  useEffect(() => {
    if (active.length < prevActive.current) reloadCompleted();
    prevActive.current = active.length;
  }, [active.length, reloadCompleted]);

  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={refetch} />;

  return (
    <div>
      <div className="card-title">Downloading ({active.length})</div>
      {active.length === 0 ? (
        <EmptyState icon="📥" title="Nothing downloading" hint="No active downloads in the managarr category." />
      ) : (
        <div className="list">
          {active.map((tor) => {
            const p = pct(tor.progress);
            const finishing = p >= 100 || tor.state === "uploading" || tor.state === "stalledUP";
            return (
              <div className="list-item" key={tor.hash}>
                <div className="list-item-main">
                  <div className="list-item-title">{tor.name}</div>
                  <div className="list-item-sub row" style={{ gap: 14 }}>
                    <span className="badge badge-muted">{tor.state}</span>
                    <span>{formatBytes(tor.size)}</span>
                    <span>↓ {formatBytes(tor.dlspeed)}/s</span>
                    <span>{tor.num_seeds} seeds</span>
                    <span>ETA {formatEta(tor.eta)}</span>
                  </div>
                </div>
                <div style={{ width: 180 }}>
                  <div className="progress">
                    <div className={"progress-fill" + (finishing ? " done" : "")} style={{ width: `${p}%` }} />
                  </div>
                  <div className="faint" style={{ fontSize: 11, marginTop: 3, textAlign: "right" }}>{p}%</div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", marginTop: 22 }}>
        <div className="card-title" style={{ margin: 0 }}>Completed / seeding ({completed.items.length}{completed.done ? "" : "+"})</div>
        <button className="btn btn-ghost btn-sm" onClick={completed.reload}><IconRefresh size={14} /> Refresh</button>
      </div>
      {completed.items.length === 0 ? (
        <div className="faint" style={{ marginTop: 8 }}>{completed.loading ? "Loading…" : "No completed downloads in the managarr category."}</div>
      ) : (
        <>
          <div className="list">
            {completed.items.map((tor) => (
              <div className="list-item" key={tor.hash}>
                <span className="badge badge-ok">done</span>
                <div className="list-item-main">
                  <div className="list-item-title">{tor.name}</div>
                  <div className="list-item-sub row" style={{ gap: 14 }}>
                    <span className="badge badge-muted">{tor.state}</span>
                    <span>{formatBytes(tor.size)}</span>
                    <span>ratio {tor.ratio != null ? tor.ratio.toFixed(2) : "—"}</span>
                    <span>{tor.num_seeds} seeds</span>
                  </div>
                </div>
                <span className="faint" style={{ fontSize: 12 }}>{timeAgo(tsToIso(tor.completion_on))}</span>
              </div>
            ))}
          </div>
          {!completed.done && <LoadMore onClick={completed.loadMore} loading={completed.loading} />}
        </>
      )}
    </div>
  );
}

function History() {
  const t = useToast();
  const [evtKind, setEvtKind] = usePersistedState("activity.history.kind", "all");
  const [limit, setLimit] = useState(PAGE);
  const [importing, setImporting] = useState(false);

  // Reset the window when the type filter changes.
  useEffect(() => { setLimit(PAGE); }, [evtKind]);

  const params = useMemo(
    () => ({ limit, ...(evtKind !== "all" ? { kind: evtKind } : {}) }),
    [limit, evtKind]
  );
  const { data, loading, error, refetch } = useApi("/grabs", params);
  const kindsApi = useApi("/grabs/event-kinds");

  async function triggerImport() {
    setImporting(true);
    try {
      await api.post("/grabs/import");
      t.success("Import triggered");
    } catch (e) {
      t.error(e.message);
    } finally {
      setImporting(false);
    }
  }

  if (loading && !data) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={refetch} />;

  const grabs = data?.grabs || [];
  const events = data?.events || [];               // already filtered by kind server-side
  const eventKinds = kindsApi.data || [];
  const hasMore = events.length >= limit || grabs.length >= limit;

  return (
    <div>
      <div className="row mb-3" style={{ justifyContent: "space-between" }}>
        <select className="input" value={evtKind} onChange={(e) => setEvtKind(e.target.value)} style={{ maxWidth: 200 }}>
          <option value="all">All event types</option>
          {eventKinds.map((k) => <option key={k} value={k}>{k}</option>)}
        </select>
        <div className="row gap-sm">
          <button className="btn btn-secondary btn-sm" onClick={triggerImport} disabled={importing}>
            <IconDownload size={14} /> {importing ? "Importing…" : "Trigger import"}
          </button>
          <button className="btn btn-ghost btn-sm" onClick={refetch}><IconRefresh size={14} /> Refresh</button>
        </div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="card-title">Grabs ({grabs.length}{grabs.length >= limit ? "+" : ""})</div>
          {grabs.length === 0 ? <div className="faint">No grabs yet.</div> : (
            <div className="list">
              {grabs.map((g) => {
                const to = itemLink(g.media_kind, g.item_id);
                const inner = (
                  <>
                    <span className={"badge " + stateBadge(g.state)}>{g.state}</span>
                    <div className="list-item-main">
                      <div className="list-item-title">{g.title}</div>
                      <div className="list-item-sub">{[g.media_kind, g.indexer, g.source].filter(Boolean).join(" · ")}</div>
                    </div>
                    <span className="faint" style={{ fontSize: 12 }}>{timeAgo(g.created_at)}</span>
                  </>
                );
                return to
                  ? <Link className="list-item list-item-link" to={to} key={g.id}>{inner}</Link>
                  : <div className="list-item" key={g.id}>{inner}</div>;
              })}
            </div>
          )}
        </div>

        <div className="card">
          <div className="card-title">
            Events ({events.length}{events.length >= limit ? "+" : ""}{evtKind !== "all" ? ` · ${evtKind}` : ""})
          </div>
          {events.length === 0 ? (
            <div className="faint">{evtKind !== "all" ? "No events of this type." : "No events yet."}</div>
          ) : (
            <div className="list">
              {events.map((e) => {
                const to = itemLink(e.media_kind, e.meta?.item_id);
                const inner = (
                  <>
                    <span className={"badge " + eventBadge(e.kind)}>{e.kind}</span>
                    <div className="list-item-main">
                      <div className="list-item-title">{e.title || e.message || "—"}</div>
                      {e.title && e.message && <div className="list-item-sub">{e.message}</div>}
                    </div>
                    <span className="faint" style={{ fontSize: 12 }}>{timeAgo(e.created_at)}</span>
                  </>
                );
                return to
                  ? <Link className="list-item list-item-link" to={to} key={e.id}>{inner}</Link>
                  : <div className="list-item" key={e.id}>{inner}</div>;
              })}
            </div>
          )}
        </div>
      </div>

      {hasMore && <LoadMore onClick={() => setLimit((l) => l + PAGE)} loading={loading} />}
    </div>
  );
}

function BlocklistView() {
  const t = useToast();
  const { items, loading, error, done, loadMore, reload } = usePagedList("/grabs/blocklist");

  const remove = useCallback(async (id) => {
    try {
      await api.del(`/grabs/blocklist/${id}`);
      t.success("Removed from blocklist");
      reload();
    } catch (e) {
      t.error(e.message);
    }
  }, [reload, t]);

  if (loading && items.length === 0) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={reload} />;
  if (items.length === 0) return <EmptyState icon="🚫" title="Blocklist empty" hint="Failed/rejected releases land here." />;

  return (
    <div>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>Title</th><th>Infohash</th><th>Reason</th><th>Added</th><th></th></tr>
          </thead>
          <tbody>
            {items.map((b) => (
              <tr key={b.id}>
                <td>{b.title || "—"}</td>
                <td className="faint" style={{ fontFamily: "monospace", fontSize: 12 }}>{b.infohash || "—"}</td>
                <td>{b.reason || "—"}</td>
                <td className="faint">{timeAgo(b.created_at)}</td>
                <td><button className="btn btn-danger btn-xs" onClick={() => remove(b.id)}><IconTrash size={12} /></button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!done && <LoadMore onClick={loadMore} loading={loading} />}
    </div>
  );
}

// Link a grab/event to its library item when we know it, else to that media-kind's
// list page; null when there's no media kind to link to.
const KIND_PATH = { movie: "movies", tv: "tv", audiobook: "audiobooks" };
function itemLink(media_kind, item_id) {
  const base = KIND_PATH[media_kind];
  if (!base) return null;
  return item_id ? `/${base}/${item_id}` : `/${base}`;
}

function stateBadge(s) {
  if (s === "imported") return "badge-ok";
  if (s === "failed") return "badge-error";
  if (s === "downloading" || s === "importing") return "badge-info";
  return "badge-muted";
}
function eventBadge(k) {
  if (["imported", "transcoded", "grabbed"].includes(k)) return "badge-ok";
  if (["failed", "error"].includes(k)) return "badge-error";
  if (k === "scan") return "badge-info";
  return "badge-muted";
}
