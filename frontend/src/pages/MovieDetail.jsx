import React, { useState } from "react";
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
import { formatDuration, providers, trailers } from "../utils/format";
import { isTvApp } from "../utils/tv";
import { IconRefresh, IconArrowLeft, IconShield } from "../components/icons";

export default function MovieDetail() {
  const { id } = useParams();
  const t = useToast();
  const nav = useNavigate();
  const { data: movie, loading, error, refetch, setData } = useApi(`/library/movies/${id}`);
  const [refreshing, setRefreshing] = useState(false);
  const [fixOpen, setFixOpen] = useState(false);
  const [delOpen, setDelOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [copyOpen, setCopyOpen] = useState(false);
  const [upgrading, setUpgrading] = useState(false);

  if (loading) return <Loading />;
  if (error) return <ErrorState error={error} onRetry={refetch} />;
  if (!movie) return null;

  const prov = providers(movie);
  const trs = trailers(movie);

  async function toggleMonitored() {
    try {
      await api.patch(`/library/movies/${id}/monitored`, { monitored: !movie.monitored });
      setData({ ...movie, monitored: !movie.monitored });
    } catch (e) {
      t.error(e.message);
    }
  }

  async function doDelete(deleteFiles) {
    setDeleting(true);
    try {
      await api.del(`/library/movies/${id}`, { delete_files: deleteFiles });
      t.success(deleteFiles ? "Deleted (files removed)" : "Removed from library");
      nav("/movies");
    } catch (e) {
      t.error(e.message);
      setDeleting(false);
    }
  }

  async function resyncPlex() {
    try {
      const r = await api.post(`/library/movies/${id}/resync-plex`);
      if (!r.ok) t.error(`Plex resync: ${r.reason || "no match"}`);
      else if (r.changed) t.success(`Matched to Plex: ${r.new_title}`);
      else t.info("Already matches Plex");
      refetch();
    } catch (e) {
      t.error(e.message);
    }
  }

  async function refreshMeta() {
    setRefreshing(true);
    try {
      await api.post(`/library/movies/${id}/refresh-metadata`);
      t.success("Metadata refreshed");
      refetch();
    } catch (e) {
      t.error(e.message);
    } finally {
      setRefreshing(false);
    }
  }

  // Auto-upgrade: try to grab a better release now; if none qualifies, keep watching.
  async function autoUpgrade() {
    setUpgrading(true);
    try {
      const r = await api.post(`/library/movies/${id}/upgrade`, { monitor: true });
      if (r.status === "grabbed") t.success(`Upgrading to ${r.release_title}`);
      else if (r.status === "monitoring") t.info("No better release yet — monitoring for an upgrade");
      else t.error(`Upgrade: ${r.reason || r.status}`);
      refetch();
    } catch (e) {
      t.error(e.message);
    } finally {
      setUpgrading(false);
    }
  }

  async function stopUpgrade() {
    try {
      await api.del(`/library/movies/${id}/upgrade`);
      t.success("Stopped watching for an upgrade");
      refetch();
    } catch (e) {
      t.error(e.message);
    }
  }

  return (
    <div>
      <Link to="/movies" className="btn btn-ghost btn-sm mb-3"><IconArrowLeft size={14} /> Movies</Link>
      <div className="detail-layout">
        <div>
          {movie.poster_url ? (
            <img className="detail-poster" src={movie.poster_url} alt={movie.title} />
          ) : (
            <div className="detail-poster placeholder">🎬</div>
          )}
        </div>
        <div>
          <div className="detail-header">
            <div>
              <div className="detail-title">{movie.title}</div>
              <div className="detail-meta">
                {[movie.year, formatDuration(movie.runtime_seconds), movie.video_codec].filter(Boolean).join(" · ")}
                {!movie.has_file && <span className="badge badge-error" style={{ marginLeft: 8 }}>missing file</span>}
              </div>
            </div>
            <div className="detail-header-actions">
              <span className="faint" style={{ fontSize: 12 }}>Monitored</span>
              <Switch checked={movie.monitored} onChange={toggleMonitored} />
              <button className="btn btn-secondary btn-sm" onClick={refreshMeta} disabled={refreshing}>
                <IconRefresh size={14} /> {refreshing ? "…" : "Refresh"}
              </button>
              {isTvApp && movie.has_file && (
                <button className="btn btn-primary btn-sm" onClick={() => setCopyOpen(true)}>
                  <IconShield size={14} /> Copy to device
                </button>
              )}
              <KebabMenu items={[
                ...(movie.has_file && !isTvApp ? [{ label: "Copy to device…", onClick: () => setCopyOpen(true) }] : []),
                { label: "Fix match", onClick: () => setFixOpen(true) },
                { label: "Resync from Plex", onClick: resyncPlex },
                { label: "Delete from library", onClick: () => setDelOpen(true), danger: true },
              ]} />
            </div>
          </div>

          {movie.overview && <p className="detail-overview">{movie.overview}</p>}

          {movie.folder_path && (
            <div className="faint" style={{ fontSize: 11, fontFamily: "monospace", margin: "2px 0 12px", wordBreak: "break-all" }}>
              {movie.folder_path}
            </div>
          )}

          <div className="detail-section">
            <div className="section-title">Providers</div>
            <div className="row-wrap">
              {prov.tmdb?.id && <span className="badge badge-info">TMDB {prov.tmdb.id}</span>}
              {prov.imdb?.id && <span className="badge badge-muted">IMDb {prov.imdb.id}</span>}
              {movie.collection_id && (
                <Link to={`/collections/${movie.collection_id}`} className="badge badge-accent">
                  🗂 {movie.meta?.collection?.name || "In collection"}
                </Link>
              )}
              {!prov.tmdb?.id && !prov.imdb?.id && <span className="faint">No provider ids</span>}
            </div>
          </div>

          {trs.length > 0 && (
            <div className="detail-section">
              <div className="section-title">Trailers</div>
              <div className="trailer-grid">
                {trs.filter((x) => (x.site || "").toLowerCase() === "youtube").map((x) => (
                  <iframe
                    key={x.key}
                    className="trailer-frame"
                    src={`https://www.youtube.com/embed/${x.key}`}
                    title="Trailer"
                    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                    allowFullScreen
                  />
                ))}
              </div>
            </div>
          )}

          {movie.has_file && (
            <div className="detail-section">
              <div className="section-title">Upgrade quality</div>
              {movie.meta?.upgrade ? (
                <div className="row gap-sm" style={{ alignItems: "center" }}>
                  <span className="badge badge-info">Upgrading…</span>
                  <span className="faint" style={{ fontSize: 12 }}>
                    {movie.meta.upgrade.release_title} — replaces the current file once it imports.
                  </span>
                  <button className="btn btn-ghost btn-xs" onClick={stopUpgrade}>Cancel</button>
                </div>
              ) : movie.meta?.upgrade_wanted ? (
                <div className="row gap-sm" style={{ alignItems: "center" }}>
                  <span className="badge badge-muted">Monitoring</span>
                  <span className="faint" style={{ fontSize: 12 }}>
                    Watching for a better release — it'll grab and swap automatically.
                  </span>
                  <button className="btn btn-ghost btn-xs" onClick={stopUpgrade}>Stop</button>
                </div>
              ) : (
                <div className="row gap-sm" style={{ alignItems: "center" }}>
                  <button className="btn btn-secondary btn-sm" onClick={autoUpgrade} disabled={upgrading}>
                    {upgrading ? "…" : "Find better & upgrade"}
                  </button>
                  <span className="faint" style={{ fontSize: 12 }}>
                    Grabs the best profile-passing release; if only a cam/ts exists, it keeps watching.
                  </span>
                </div>
              )}
            </div>
          )}

          <div className="detail-section">
            <div className="section-title">{movie.has_file ? "Pick a release to upgrade to" : "Search releases"}</div>
            <ReleaseSearch
              mediaKind="movie"
              defaultQuery={movie.year ? `${movie.title} ${movie.year}` : movie.title}
              grabContext={{ item_id: movie.id, title: movie.title, year: movie.year }}
              upgradeMovieId={movie.has_file ? movie.id : null}
              onGrabbed={refetch}
            />
          </div>
        </div>
      </div>
      <FixMatchModal
        open={fixOpen}
        onClose={() => setFixOpen(false)}
        kind="movie"
        kindPath="movies"
        id={id}
        initialQuery={movie.title}
        onApplied={refetch}
      />
      <DeleteDialog
        open={delOpen}
        title="Delete movie"
        message={`Remove “${movie.title}” from the library?`}
        fileHint="“Keep files” leaves the movie on disk (a rescan re-adds it). “Delete files” permanently removes its folder and any in-progress download."
        busy={deleting}
        onClose={() => setDelOpen(false)}
        onConfirm={doDelete}
      />
      <CopyToDeviceModal
        open={copyOpen}
        onClose={() => setCopyOpen(false)}
        mediaKind="movie"
        itemId={movie.id}
        title={movie.year ? `${movie.title} (${movie.year})` : movie.title}
      />
    </div>
  );
}
