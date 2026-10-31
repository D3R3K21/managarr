import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import api from "../api/client";
import Modal from "./Modal";

// meta.trailers = [{site, key}] (YouTube). Accept either item.trailers or item.meta.trailers.
function trailerList(item) {
  const t = (item && (item.trailers || (item.meta && item.meta.trailers))) || [];
  return (Array.isArray(t) ? t : []).filter((x) => (x.site || "").toLowerCase() === "youtube" && x.key);
}

const TMDB_IMG = "https://image.tmdb.org/t/p/w342";

function poster(item) {
  if (!item) return null;
  return (
    item.poster_url ||
    item.cover ||
    item.cover_url ||
    (item.poster_path ? TMDB_IMG + item.poster_path : null) ||
    null
  );
}

function title(item) {
  return (item && (item.title || item.name)) || "Untitled";
}

function year(item) {
  if (!item) return null;
  const d = item.release_date || item.first_air_date || item.published_year || item.year || "";
  return String(d).slice(0, 4) || null;
}

function rating(item) {
  if (!item) return null;
  const v = item.vote_average ?? item.rating;
  if (v == null) return null;
  const n = Number(v);
  return isNaN(n) ? String(v) : n.toFixed(1);
}

function genres(item) {
  const g = (item && (item.genres || item.genre)) || [];
  if (Array.isArray(g)) return g.map((x) => (typeof x === "string" ? x : x && x.name)).filter(Boolean);
  if (typeof g === "string") return g.split(/[,/]/).map((s) => s.trim()).filter(Boolean);
  return [];
}

// Provider links present on the item or its meta blob.
function providerLinks(item) {
  if (!item) return [];
  const out = [];
  const meta = item.meta || {};
  const prov = meta.providers || {};

  const tmdbId = item.tmdb_id || (prov.tmdb && prov.tmdb.id) || (item.media_type !== "tv" ? item.id : null);
  const tvdbId = item.tvdb_id || (prov.tvdb && prov.tvdb.id);
  const imdbId = item.imdb_id || (prov.imdb && prov.imdb.id);
  const asin = item.asin;

  if (tmdbId) {
    const seg = item.first_air_date || item.name ? "tv" : "movie";
    out.push({ label: `TMDB ${tmdbId}`, href: `https://www.themoviedb.org/${seg}/${tmdbId}`, cls: "badge-info" });
  }
  if (tvdbId) out.push({ label: `TVDB ${tvdbId}`, href: `https://thetvdb.com/dereferrer/series/${tvdbId}`, cls: "badge-accent" });
  if (imdbId) out.push({ label: `IMDb ${imdbId}`, href: `https://www.imdb.com/title/${imdbId}/`, cls: "badge-muted" });
  if (asin) out.push({ label: `ASIN ${asin}`, href: `https://www.audible.com/pd/${asin}`, cls: "badge-muted" });
  return out;
}

// Generic metadata viewer for a discover/library/collection item.
// props: open, onClose, item, action? (footer node, e.g. a grab button),
//        detailTo? (route string — makes the title a link to the detail page)
export default function MediaInfoModal({ open, onClose, item, action, detailTo }) {
  const metaTrailers = item ? trailerList(item) : [];
  const isBook = !!(item && (item.asin || item.author || item.authors));
  const [ytTrailers, setYtTrailers] = useState([]);

  // Fallback: no trailer in the TMDB metadata -> ask the backend to search YouTube
  // for "<title> <year> trailer" (books excluded — a trailer search is meaningless).
  useEffect(() => {
    setYtTrailers([]);
    if (!open || !item || isBook || metaTrailers.length > 0) return;
    let cancelled = false;
    const q = [title(item), year(item)].filter(Boolean).join(" ");
    api.get("/discover/trailers", { q })
      .then((d) => { if (!cancelled) setYtTrailers((d && d.trailers) || []); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [open, item]);

  if (!open || !item) return null;
  const img = poster(item);
  const overview = item.overview || item.description || item.synopsis || "";
  const r = rating(item);
  const gs = genres(item);
  const links = providerLinks(item);
  const author = item.author || item.authors;
  const y = year(item);
  const trs = metaTrailers.length > 0 ? metaTrailers : ytTrailers;

  const heading = detailTo ? (
    <Link to={detailTo} onClick={onClose} className="modal-title-link">{title(item)}</Link>
  ) : (
    title(item)
  );

  return (
    <Modal open={open} onClose={onClose} title={heading} width={620} footer={action}>
      <div className="info-modal">
        <div className="info-poster">
          {img ? (
            <img src={img} alt={title(item)} />
          ) : (
            <div className="info-poster-ph">🎬</div>
          )}
        </div>
        <div className="info-meta">
          <div className="info-sub">
            {[author, y, r ? `★ ${r}` : null].filter(Boolean).join(" · ") || <span className="faint">No details</span>}
            {item.in_library && <span className="badge badge-ok" style={{ marginLeft: 8 }}>in library</span>}
          </div>

          {gs.length > 0 && (
            <div className="row-wrap mt-2">
              {gs.map((g) => (
                <span key={g} className="badge badge-muted">{g}</span>
              ))}
            </div>
          )}

          {overview && <p className="detail-overview" style={{ marginTop: 12 }}>{overview}</p>}

          {trs.length > 0 && (
            <div className="detail-section" style={{ marginTop: 12, marginBottom: 0 }}>
              <div className="section-title">Trailer{trs.length > 1 ? "s" : ""}</div>
              <div className="trailer-grid">
                {trs.map((x) => (
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

          {links.length > 0 && (
            <div className="detail-section" style={{ marginTop: 12, marginBottom: 0 }}>
              <div className="section-title">Links</div>
              <div className="row-wrap">
                {links.map((l) => (
                  <a key={l.label} className={"badge " + l.cls} href={l.href} target="_blank" rel="noreferrer">
                    {l.label}
                  </a>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}
