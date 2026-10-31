import React, { useState, useEffect, useCallback } from "react";
import api from "../api/client";
import { Loading, ErrorState } from "./States";
import { IconArrowLeft, IconLibrary } from "./icons";

// Filesystem browser backed by GET /api/library/browse?path=...
// Calls onChange(currentPath) whenever the browsed path changes so the parent
// can use the current folder as the selection.
export default function DirectoryPicker({ initialPath = "/", onChange }) {
  const [path, setPath] = useState(initialPath);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async (p) => {
    setLoading(true);
    setError(null);
    try {
      const d = await api.get("/library/browse", { path: p });
      setData(d);
      setPath(d.path || p);
      if (onChange) onChange(d.path || p);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
    // onChange intentionally excluded — parent passes a stable setter
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    load(initialPath);
  }, [load, initialPath]);

  const dirs = (data && data.dirs) || [];
  const parent = data && data.parent;

  return (
    <div>
      <div className="row mb-2" style={{ gap: 8 }}>
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => load(parent)}
          disabled={loading || !parent}
          title="Up one level"
        >
          <IconArrowLeft size={14} /> Up
        </button>
        <code className="dir-current" title={path}>{path}</code>
      </div>

      <div className="dir-list">
        {loading && <Loading label="Reading directory…" />}
        {error && <ErrorState error={error} onRetry={() => load(path)} />}
        {!loading && !error && dirs.length === 0 && (
          <div className="faint" style={{ padding: "12px 4px" }}>No subfolders here.</div>
        )}
        {!loading && !error && dirs.map((d) => (
          <button key={d.path} className="dir-row" onClick={() => load(d.path)}>
            <IconLibrary size={15} />
            <span className="dir-name">{d.name}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
