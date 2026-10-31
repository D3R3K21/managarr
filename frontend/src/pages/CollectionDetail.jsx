import React, { useState } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import api from "../api/client";
import { useApi } from "../hooks/useApi";
import { useToast } from "../hooks/useToast";
import { Loading, ErrorState } from "../components/States";
import MediaInfoModal from "../components/MediaInfoModal";
import KebabMenu from "../components/KebabMenu";
import Switch from "../components/Switch";
import { IconArrowLeft, IconDownload, IconRefresh } from "../components/icons";

function memberCover(m) {
  return (m.meta && m.meta.cover_url) || null;
}

function memberPath(m) {
  return m.item_kind === "audiobook" ? "audiobooks" : "movies";
}

export default function CollectionDetail() {
  const { id } = useParams();
  const t = useToast();
  const nav = useNavigate();
  const { data, loading, error, refetch } = useApi(`/collections/${id}`);
  const [grabbing, setGrabbing] = useState(null);
  const [grabbingAll, setGrabbingAll] = useState(false);
  const [sel, setSel] = useState(null);   // { item, detailTo, member }
  const [busy, setBusy] = useState(false);

  // Resync this collection's members from Plex (adopt the match you fixed in Plex);
  // if they re-match elsewhere and this collection empties, it's auto-deleted.
  async function resyncPlex() {
    setBusy(true);
    try {
      const r = await api.post(`/collections/${id}/resync-plex`);
      const changed = (r.resynced || []).filter((x) => x.changed).length;
      const unmatched = (r.unmatched || []).length;
      if (r.collection_deleted) {
        t.success(`Re-matched from Plex — bogus collection removed`);
        nav("/collections");
        return;
      }
      t[unmatched ? "info" : "success"](
        `Resynced ${r.resynced?.length || 0} (${changed} changed${unmatched ? `, ${unmatched} with no Plex match` : ""})`);
      refetch();
    } catch (e) {
      t.error(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function deleteCollection() {
    if (!window.confirm("Delete this collection? Owned items are kept — only the grouping is removed.")) return;
    setBusy(true);
    try {
      await api.del(`/collections/${id}`);
      t.success("Collection deleted");
      nav("/collections");
    } catch (e) {
      t.error(e.message);
      setBusy(false);
    }
  }

  async function removeEntry(m) {
    if (!window.confirm(`Remove the ${m.item_kind} entry "${m.title}"? Files on disk are kept.`)) return;
    try {
      await api.del(`/library/${memberPath(m)}/${m.item_id}`);
      t.success(`Removed ${m.title}`);
      refetch();
    } catch (e) {
      t.error(e.message);
    }
  }

  // Click a member -> info modal. For owned members, pull the full library record
  // (overview + trailers) and link the modal title to its detail page.
  async function openMember(m) {
    const base = { title: m.title, year: m.year, cover_url: memberCover(m), meta: m.meta };
    if (m.owned && m.item_id) {
      const detailTo = `/${memberPath(m)}/${m.item_id}`;
      setSel({ item: base, detailTo, member: m });
      try {
        const full = await api.get(`/library/${memberPath(m)}/${m.item_id}`);
        setSel((cur) => (cur && cur.member.id === m.id
          ? { ...cur, item: { ...full, cover_url: base.cover_url || full.poster_url || full.cover_url } }
          : cur));
      } catch { /* keep the base info */ }
    } else {
      setSel({ item: base, detailTo: null, member: m });
    }
  }

  if (loading) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={refetch} />;
  if (!data) return null;

  const { collection, owned = [], missing = [] } = data;

  async function toggleMonitored() {
    try {
      await api.patch(`/collections/${id}/monitored`, { monitored: !collection.monitored });
      t.success(!collection.monitored
        ? "Watching — auto-download will fill missing members"
        : "No longer watching");
      refetch();
    } catch (e) {
      t.error(e.message);
    }
  }

  async function grab(member) {
    setGrabbing(member.id);
    try {
      await api.post(`/collections/missing/${member.id}/grab`);
      t.success(`Grabbed ${member.title}`);
      refetch();
    } catch (e) {
      t.error(`Grab failed: ${e.message}`);
    } finally {
      setGrabbing(null);
    }
  }

  async function grabAll() {
    setGrabbingAll(true);
    let ok = 0, fail = 0;
    for (const m of missing) {
      try {
        await api.post(`/collections/missing/${m.id}/grab`);
        ok++;
      } catch {
        fail++;
      }
    }
    t[fail ? "info" : "success"](`Grabbed ${ok}${fail ? `, ${fail} failed` : ""}`);
    setGrabbingAll(false);
    refetch();
  }

  return (
    <div>
      <Link to="/collections" className="btn btn-ghost btn-sm mb-3"><IconArrowLeft size={14} /> Collections</Link>

      <div className="row mb-3" style={{ alignItems: "flex-start", gap: 20 }}>
        {collection.poster_url ? (
          <img src={collection.poster_url} alt={collection.name} style={{ width: 130, borderRadius: 8, border: "1px solid var(--border)" }} />
        ) : null}
        <div style={{ flex: 1 }}>
          <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
            <div className="detail-title">{collection.name}</div>
            <div className="row gap-sm" style={{ alignItems: "center" }}>
              <label className="row gap-sm" style={{ alignItems: "center", fontSize: 13 }}
                title="When watched, the auto-download queue fills missing members (throttled by the auto-download cap)">
                <Switch checked={!!collection.monitored} onChange={toggleMonitored} /> Watched
              </label>
              {collection.kind === "movie" && (
                <button className="btn btn-secondary btn-sm" onClick={resyncPlex} disabled={busy}
                  title="Adopt the matches you fixed in Plex; removes this collection if it was created by a mismatch">
                  <IconRefresh size={14} /> {busy ? "…" : "Resync from Plex"}
                </button>
              )}
              <KebabMenu items={[{ label: "Delete collection", onClick: deleteCollection }]} />
            </div>
          </div>
          <div className="detail-meta">
            {collection.kind === "movie" ? "Movie franchise" : "Audiobook series"} · {owned.length} owned · {missing.length} missing
          </div>
          {collection.overview && <p className="detail-overview">{collection.overview}</p>}
          {missing.length > 0 && (
            <button className="btn btn-primary btn-sm" onClick={grabAll} disabled={grabbingAll}>
              <IconDownload size={14} /> {grabbingAll ? "Grabbing…" : `Grab all missing (${missing.length})`}
            </button>
          )}
        </div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="card-title">Owned ({owned.length})</div>
          <div className="list">
            {owned.map((m) => (
              <div className="list-item" key={m.id}>
                {memberCover(m) && <img src={memberCover(m)} alt="" style={{ width: 32, height: 48, objectFit: "cover", borderRadius: 4 }} />}
                <div className="list-item-main" style={{ cursor: "pointer" }} role="button" tabIndex={0} onClick={() => openMember(m)}>
                  <div className="list-item-title">{m.title}</div>
                  <div className="list-item-sub">{[m.position != null && `#${m.position}`, m.year].filter(Boolean).join(" · ")}</div>
                </div>
                {m.item_id ? (
                  <Link className="badge badge-ok" to={`/${memberPath(m)}/${m.item_id}`}>view</Link>
                ) : (
                  <span className="badge badge-ok">have</span>
                )}
                {m.item_id && (m.item_kind === "movie" || m.item_kind === "tv") && (
                  <button className="btn btn-ghost btn-xs" title="Remove this entry (keeps files)"
                    onClick={() => removeEntry(m)} style={{ color: "var(--err)" }}>✕</button>
                )}
              </div>
            ))}
            {owned.length === 0 && <div className="faint">None owned.</div>}
          </div>
        </div>

        <div className="card">
          <div className="card-title">Missing ({missing.length})</div>
          <div className="list">
            {missing.map((m) => (
              <div className="list-item highlight" key={m.id}>
                {memberCover(m) && <img src={memberCover(m)} alt="" style={{ width: 32, height: 48, objectFit: "cover", borderRadius: 4 }} />}
                <div className="list-item-main" style={{ cursor: "pointer" }} role="button" tabIndex={0} onClick={() => openMember(m)}>
                  <div className="list-item-title">{m.title}</div>
                  <div className="list-item-sub">{[m.position != null && `#${m.position}`, m.year].filter(Boolean).join(" · ")}</div>
                </div>
                <button className="btn btn-success btn-xs" onClick={() => grab(m)} disabled={grabbing === m.id}>
                  <IconDownload size={12} /> {grabbing === m.id ? "…" : "Grab"}
                </button>
              </div>
            ))}
            {missing.length === 0 && <div className="faint">Nothing missing 🎉</div>}
          </div>
        </div>
      </div>

      <MediaInfoModal
        open={!!sel}
        item={sel?.item}
        detailTo={sel?.detailTo}
        onClose={() => setSel(null)}
        action={sel && !sel.member.owned && (
          <button className="btn btn-success btn-sm"
            onClick={() => { grab(sel.member); setSel(null); }}
            disabled={grabbing === sel.member.id}>
            <IconDownload size={14} /> {grabbing === sel.member.id ? "…" : "Grab"}
          </button>
        )}
      />
    </div>
  );
}
