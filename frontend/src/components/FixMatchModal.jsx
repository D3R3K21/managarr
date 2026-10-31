import React, { useState } from "react";
import Modal from "./Modal";
import api from "../api/client";

// Search a metadata provider for the correct match and pin it.
// props: open, onClose, kind ("movie"|"tv"|"audiobook"), kindPath ("movies"|"tv"|"audiobooks"),
//        id, initialQuery, onApplied
export default function FixMatchModal({ open, onClose, kind, kindPath, id, initialQuery, onApplied }) {
  const [q, setQ] = useState(initialQuery || "");
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(null);
  const [err, setErr] = useState("");

  async function search(e) {
    if (e) e.preventDefault();
    setLoading(true);
    setErr("");
    try {
      setResults(await api.get("/library/match/search", { kind, q }));
    } catch (e2) {
      setErr(e2.message);
    } finally {
      setLoading(false);
    }
  }

  async function apply(item) {
    setApplying(item.id);
    setErr("");
    try {
      await api.post(`/library/${kindPath}/${id}/match`, { provider: item.provider, id: item.id });
      onApplied && onApplied();
      onClose && onClose();
    } catch (e2) {
      setErr(e2.message);
      setApplying(null);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Fix match" width={640}>
      <form onSubmit={search} className="row gap-sm" style={{ marginBottom: 12 }}>
        <input
          className="input"
          style={{ flex: 1 }}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search the correct title…"
          autoFocus
        />
        <button className="btn btn-primary btn-sm" type="submit" disabled={loading || !q.trim()}>
          {loading ? "Searching…" : "Search"}
        </button>
      </form>

      {err && <div className="badge badge-error" style={{ marginBottom: 8 }}>{err}</div>}
      {results && results.length === 0 && <div className="faint">No results — try a different search.</div>}

      <div className="match-results">
        {(results || []).map((r) => (
          <div key={r.provider + r.id} className="match-row">
            {r.poster ? (
              <img className="match-poster" src={r.poster} alt="" loading="lazy" />
            ) : (
              <div className="match-poster placeholder">🎬</div>
            )}
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="match-title">
                {r.title} {r.year && <span className="faint">({r.year})</span>}
              </div>
              {r.overview && <div className="faint match-overview">{r.overview}</div>}
              <div className="faint" style={{ fontSize: 11 }}>{r.provider} · {r.id}</div>
            </div>
            <button className="btn btn-secondary btn-sm" onClick={() => apply(r)} disabled={applying === r.id}>
              {applying === r.id ? "…" : "Use this"}
            </button>
          </div>
        ))}
      </div>
    </Modal>
  );
}
