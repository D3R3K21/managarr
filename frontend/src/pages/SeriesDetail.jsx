import React, { useState, useMemo } from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import api from "../api/client";
import { useApi } from "../hooks/useApi";
import { useToast } from "../hooks/useToast";
import Switch from "../components/Switch";
import KebabMenu from "../components/KebabMenu";
import FixMatchModal from "../components/FixMatchModal";
import DeleteDialog from "../components/DeleteDialog";
import CopyToDeviceModal from "../components/CopyToDeviceModal";
import ReleaseSearch from "../components/ReleaseSearch";
import { Loading, ErrorState } from "../components/States";
import { providers, trailers, formatDate } from "../utils/format";
import { IconRefresh, IconArrowLeft, IconChevron, IconShield } from "../components/icons";

export default function SeriesDetail() {
  const { id } = useParams();
  const t = useToast();
  const nav = useNavigate();
  const { data, loading, error, refetch, setData } = useApi(`/library/tv/${id}`);
  const [refreshing, setRefreshing] = useState(false);
  const [fixOpen, setFixOpen] = useState(false);
  const [delOpen, setDelOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [openSeasons, setOpenSeasons] = useState({});
  const [copyEp, setCopyEp] = useState(null);

  const series = data?.series;
  const episodes = data?.episodes || [];

  const seasons = useMemo(() => {
    const map = {};
    for (const ep of episodes) {
      (map[ep.season] ||= []).push(ep);
    }
    return Object.entries(map)
      .map(([s, eps]) => [Number(s), eps.sort((a, b) => a.episode - b.episode)])
      .sort((a, b) => a[0] - b[0]);
  }, [episodes]);

  if (loading) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={refetch} />;
  if (!series) return null;

  const prov = providers(series);
  const trs = trailers(series);
  const missingCount = episodes.filter((e) => !e.has_file).length;

  async function toggleMonitored() {
    try {
      await api.patch(`/library/tv/${id}/monitored`, { monitored: !series.monitored });
      setData({ ...data, series: { ...series, monitored: !series.monitored } });
    } catch (e) {
      t.error(e.message);
    }
  }

  async function doDelete(deleteFiles) {
    setDeleting(true);
    try {
      await api.del(`/library/tv/${id}`, { delete_files: deleteFiles });
      t.success(deleteFiles ? "Deleted (files removed)" : "Removed from library");
      nav("/tv");
    } catch (e) {
      t.error(e.message);
      setDeleting(false);
    }
  }

  async function refreshMeta() {
    setRefreshing(true);
    try {
      await api.post(`/library/tv/${id}/refresh-metadata`);
      t.success("Metadata refreshed");
      refetch();
    } catch (e) {
      t.error(e.message);
    } finally {
      setRefreshing(false);
    }
  }

  async function resyncPlex() {
    try {
      const r = await api.post(`/library/tv/${id}/resync-plex`);
      if (!r.ok) t.error(`Plex resync: ${r.reason || "no match"}`);
      else if (r.changed) t.success(`Matched to Plex: ${r.new_title}`);
      else t.info("Already matches Plex");
      refetch();
    } catch (e) {
      t.error(e.message);
    }
  }

  return (
    <div>
      <Link to="/tv" className="btn btn-ghost btn-sm mb-3"><IconArrowLeft size={14} /> TV</Link>
      <div className="detail-layout">
        <div>
          {series.poster_url ? (
            <img className="detail-poster" src={series.poster_url} alt={series.title} />
          ) : (
            <div className="detail-poster placeholder">📺</div>
          )}
        </div>
        <div>
          <div className="detail-header">
            <div>
              <div className="detail-title">{series.title}</div>
              <div className="detail-meta">
                {[series.year, series.status].filter(Boolean).join(" · ")}
                {missingCount > 0 && <span className="badge badge-error" style={{ marginLeft: 8 }}>{missingCount} missing</span>}
              </div>
            </div>
            <div className="detail-header-actions">
              <span className="faint" style={{ fontSize: 12 }}>Monitored</span>
              <Switch checked={series.monitored} onChange={toggleMonitored} />
              <button className="btn btn-secondary btn-sm" onClick={refreshMeta} disabled={refreshing}>
                <IconRefresh size={14} /> {refreshing ? "…" : "Refresh"}
              </button>
              <KebabMenu items={[
                { label: "Fix match", onClick: () => setFixOpen(true) },
                { label: "Resync from Plex", onClick: resyncPlex },
                { label: "Delete from library", onClick: () => setDelOpen(true), danger: true },
              ]} />
            </div>
          </div>

          {series.overview && <p className="detail-overview">{series.overview}</p>}

          {series.folder_path && (
            <div className="faint" style={{ fontSize: 11, fontFamily: "monospace", margin: "2px 0 12px", wordBreak: "break-all" }}>
              {series.folder_path}
            </div>
          )}

          <div className="detail-section">
            <div className="section-title">Providers</div>
            <div className="row-wrap">
              {prov.tvdb?.id && <span className="badge badge-info">TVDB {prov.tvdb.id}</span>}
              {prov.tmdb?.id && <span className="badge badge-muted">TMDB {prov.tmdb.id}</span>}
              {!prov.tvdb?.id && !prov.tmdb?.id && <span className="faint">No provider ids</span>}
            </div>
          </div>

          {trs.length > 0 && (
            <div className="detail-section">
              <div className="section-title">Trailers</div>
              <div className="trailer-grid">
                {trs.filter((x) => (x.site || "").toLowerCase() === "youtube").map((x) => (
                  <iframe key={x.key} className="trailer-frame" src={`https://www.youtube.com/embed/${x.key}`} title="Trailer" allowFullScreen />
                ))}
              </div>
            </div>
          )}

          <div className="detail-section">
            <div className="section-title">Seasons &amp; Episodes</div>
            {seasons.length === 0 && <div className="faint">No episodes recorded.</div>}
            <div className="col">
              {seasons.map(([sn, eps]) => {
                const open = openSeasons[sn] ?? sn === seasons[0]?.[0];
                const seasonMissing = eps.filter((e) => !e.has_file).length;
                return (
                  <div className="card" key={sn} style={{ padding: 0 }}>
                    <button
                      className="row"
                      style={{ width: "100%", padding: "12px 14px", background: "none", border: "none", color: "inherit", cursor: "pointer" }}
                      onClick={() => setOpenSeasons((o) => ({ ...o, [sn]: !open }))}
                    >
                      <span style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform .15s", display: "inline-flex" }}>
                        <IconChevron size={16} />
                      </span>
                      <span style={{ fontWeight: 700 }}>Season {sn}</span>
                      <span className="faint" style={{ fontSize: 12 }}>{eps.length} eps</span>
                      {seasonMissing > 0 && <span className="badge badge-error">{seasonMissing} missing</span>}
                    </button>
                    {open && (
                      <div className="list" style={{ padding: "0 14px 14px" }}>
                        {eps.map((ep) => (
                          <div className={"list-item" + (!ep.has_file ? " highlight" : "")} key={ep.id}>
                            <span className="faint" style={{ minWidth: 48 }}>
                              S{String(ep.season).padStart(2, "0")}E{String(ep.episode).padStart(2, "0")}
                            </span>
                            <div className="list-item-main">
                              <div className="list-item-title">{ep.title || "Untitled"}</div>
                              <div className="list-item-sub">{formatDate(ep.air_date)}</div>
                            </div>
                            {ep.has_file ? (
                              <>
                                <span className="badge badge-ok">have</span>
                                <button className="btn btn-ghost btn-xs" title="Copy to device"
                                        onClick={() => setCopyEp(ep)}>
                                  <IconShield size={13} />
                                </button>
                              </>
                            ) : (
                              <span className="badge badge-error">missing</span>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          <div className="detail-section">
            <div className="section-title">Search releases</div>
            <ReleaseSearch
              mediaKind="tv"
              defaultQuery={series.title}
              grabContext={{ item_id: series.id, title: series.title }}
            />
          </div>
        </div>
      </div>
      <FixMatchModal
        open={fixOpen}
        onClose={() => setFixOpen(false)}
        kind="tv"
        kindPath="tv"
        id={id}
        initialQuery={series.title}
        onApplied={refetch}
      />
      <DeleteDialog
        open={delOpen}
        title="Delete series"
        message={`Remove “${series.title}” from the library?`}
        fileHint="“Keep files” leaves the show on disk (a rescan re-adds it). “Delete files” permanently removes the whole series folder (all seasons) and any in-progress download."
        busy={deleting}
        onClose={() => setDelOpen(false)}
        onConfirm={doDelete}
      />
      <CopyToDeviceModal
        open={!!copyEp}
        onClose={() => setCopyEp(null)}
        mediaKind="tv"
        itemId={copyEp?.id}
        title={copyEp ? `${series.title} S${String(copyEp.season).padStart(2, "0")}E${String(copyEp.episode).padStart(2, "0")}` : ""}
      />
    </div>
  );
}
