import React, { useState, useEffect, useCallback, useRef } from "react";
import { useNavigate } from "react-router-dom";
import api from "../api/client";
import { useToast } from "../hooks/useToast";
import { isTvApp } from "../utils/tv";
import MediaInfoModal from "./MediaInfoModal";
import { IconDownload, IconPlus, IconSearch, IconX } from "./icons";

// The app's single search bar, always visible in the topbar: library + TMDB/Audible
// in a dropdown, available on every page. Kind chips ("Search in Movies…") re-query
// restricted to one media type. Rows are clickable: owned -> its library page,
// otherwise the info modal with Add / Add+Download.
const KIND_ICON = { movie: "🎬", tv: "📺", audiobook: "📚" };
const KIND_CHIPS = [["", "All"], ["movie", "Movies"], ["tv", "TV"], ["audiobook", "Books"]];
const sourceLabel = (s) => (s === "audible" ? "Audible" : s === "tmdb" ? "TMDB" : "library");
const detailPath = (kind, id) =>
  kind === "movie" ? `/movies/${id}` : kind === "tv" ? `/tv/${id}` : `/audiobooks/${id}`;

export default function GlobalSearch() {
  const t = useToast();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");        // "" = all media types
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [adding, setAdding] = useState({});   // "kind:id" -> true while in flight
  const [added, setAdded] = useState({});      // "kind:id" -> "library" | "download"
  const [sel, setSel] = useState(null);        // result opened in the info modal
  const timer = useRef(null);
  const boxRef = useRef(null);
  const inputRef = useRef(null);

  const collapse = useCallback(() => { setOpen(false); }, []);

  const run = useCallback(async (query, k) => {
    if ((query || "").trim().length < 2) { setData(null); return; }
    setLoading(true);
    try {
      const params = { q: query, library: true };
      if (k) params.kind = k;
      setData(await api.get("/search", params));
    } catch (e) { t.error(e.message); }
    finally { setLoading(false); }
  }, [t]);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => run(q, kind), 350);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [q, kind, run]);

  useEffect(() => {
    function onDoc(e) { if (boxRef.current && !boxRef.current.contains(e.target)) collapse(); }
    function onKey(e) { if (e.key === "Escape") collapse(); }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [collapse]);

  async function add(r, download) {
    const key = `${r.kind}:${r.provider_id}`;
    setAdding((a) => ({ ...a, [key]: true }));
    try {
      await api.post("/search/add", {
        kind: r.kind, provider: r.provider, provider_id: r.provider_id,
        title: r.title, year: r.year, author: r.author || "", series: r.series || "", download,
      });
      t.success(download ? `Added + downloading: ${r.title}` : `Added: ${r.title}`);
      setAdded((s) => ({ ...s, [key]: download ? "download" : "library" }));
      setSel(null);
    } catch (e) {
      t.error(e.message);
    } finally {
      setAdding((a) => { const n = { ...a }; delete n[key]; return n; });
    }
  }

  function openResult(r) {
    if (r.owned && r.library_id) {
      collapse();
      nav(detailPath(r.kind, r.library_id));
    } else {
      setSel(r);
    }
  }

  function modalAction(r) {
    if (!r) return null;
    const key = `${r.kind}:${r.provider_id}`;
    const busy = adding[key];
    if (added[key]) return <span className="badge badge-ok">{added[key] === "download" ? "Grabbing…" : "Added"}</span>;
    return (
      <div className="row gap-sm">
        <button className="btn btn-ghost btn-sm" disabled={!!busy} onClick={() => add(r, false)}>
          <IconPlus size={14} /> {busy ? "…" : "Add"}
        </button>
        <button className="btn btn-success btn-sm" disabled={!!busy} onClick={() => add(r, true)}>
          <IconDownload size={14} /> {busy ? "…" : "Add + Download"}
        </button>
      </div>
    );
  }

  const results = data?.results || [];
  const showDrop = open && q.trim().length >= 2;

  // TV/remote: the dropdown has no click-outside to close it, and a focused text input
  // swallows D-pad arrows. Close when focus truly leaves the box, and let ArrowDown jump
  // from the input into the results (and Escape close it).
  const onBoxBlur = () => {
    if (!isTvApp) return;
    setTimeout(() => { if (!boxRef.current?.contains(document.activeElement)) collapse(); }, 0);
  };
  const onInputKeyDown = (e) => {
    if (e.key === "Escape") { collapse(); return; }
    if (e.key === "ArrowDown" && showDrop) {
      const first = boxRef.current?.querySelector(".gs-row, .gs-filters .chip");
      if (first) { e.preventDefault(); first.focus(); }
    }
  };

  return (
    <div className="global-search" ref={boxRef} onBlur={onBoxBlur}>
      <div className="global-search-input">
        <IconSearch size={15} className="gs-input-icon" />
        <input
          ref={inputRef}
          className="input search-bar"
          placeholder="Search movies, TV, audiobooks…"
          value={q}
          onFocus={() => setOpen(true)}
          onKeyDown={onInputKeyDown}
          onChange={(e) => { setQ(e.target.value); setOpen(true); }}
        />
        {q && (
          <button className="gs-close" aria-label="Clear search"
            onClick={() => { setQ(""); setKind(""); setData(null); setOpen(false); inputRef.current?.focus(); }}>
            <IconX size={15} />
          </button>
        )}
      </div>
      {showDrop && (
        <div className="global-search-results">
          <div className="gs-filters">
            {KIND_CHIPS.map(([k, label]) => (
              <button
                key={k}
                className={"chip chip-sm" + (kind === k ? " active" : "")}
                onClick={() => setKind(k)}
                title={k ? `Only show ${label.toLowerCase()}` : "All media types"}
              >
                {k ? `${KIND_ICON[k]} ${kind === k ? label : `Search in ${label}`}` : label}
              </button>
            ))}
          </div>
          {loading && !data && <div className="faint gs-msg">Searching…</div>}
          {!loading && results.length === 0 && <div className="faint gs-msg">No matches</div>}
          {results.map((r) => {
            const akey = `${r.kind}:${r.provider_id}`;
            const isAdded = added[akey];
            const busy = adding[akey];
            return (
              <div className="gs-row gs-row-click" role="button" tabIndex={0}
                key={`${r.kind}:${r.provider_id || "lib" + r.library_id}`}
                onClick={() => openResult(r)}
                onKeyDown={(e) => e.key === "Enter" && openResult(r)}>
                {r.cover_url
                  ? <img src={r.cover_url} alt="" className="gs-poster" />
                  : <span className="gs-poster gs-poster-ph">{KIND_ICON[r.kind] || "🔍"}</span>}
                <div className="gs-main">
                  <div className="gs-title">{r.title}</div>
                  <div className="gs-sub">{[KIND_ICON[r.kind], r.sub, r.year].filter(Boolean).join(" · ")}</div>
                </div>
                {r.owned ? (
                  <span className="badge badge-ok">in library</span>
                ) : isAdded ? (
                  <span className="badge badge-ok">{isAdded === "download" ? "grabbing" : "added"}</span>
                ) : (
                  <div className="row gap-sm" onClick={(e) => e.stopPropagation()}>
                    <button className="btn btn-ghost btn-xs" disabled={busy} onClick={() => add(r, false)} title="Add to library">
                      <IconPlus size={12} />
                    </button>
                    <button className="btn btn-success btn-xs" disabled={busy} onClick={() => add(r, true)} title="Add + download">
                      <IconDownload size={12} />
                    </button>
                  </div>
                )}
              </div>
            );
          })}
          {results.length > 0 && (
            <div className="gs-foot faint">Library + TMDB / Audible</div>
          )}
        </div>
      )}

      <MediaInfoModal
        open={!!sel}
        item={sel ? { ...sel, poster_url: sel.cover_url, in_library: sel.owned } : null}
        detailTo={sel && sel.owned && sel.library_id ? detailPath(sel.kind, sel.library_id) : null}
        onClose={() => setSel(null)}
        action={modalAction(sel)}
      />
    </div>
  );
}
