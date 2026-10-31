import React, { useState, useEffect, useRef, useCallback } from "react";
import api from "../api/client";
import { useToast } from "../hooks/useToast";
import { IconRefresh } from "./icons";

const KINDS = [
  { key: "movie", label: "Movies", path: "/scan/movies" },
  { key: "tv", label: "TV", path: "/scan/tv" },
  { key: "audiobook", label: "Audiobooks", path: "/scan/audiobooks" },
];

function percent(s) {
  if (!s || !s.total) return 0;
  return Math.max(0, Math.min(100, Math.round((s.processed / s.total) * 100)));
}

// Scan launcher + live progress. props: compact? (hides the heading/subtitle)
export default function ScanPanel({ compact = false }) {
  const t = useToast();
  const [status, setStatus] = useState({});
  const [starting, setStarting] = useState(null);
  const timer = useRef(null);
  const mounted = useRef(true);

  const anyRunning = Object.values(status).some((s) => s && s.running);

  const poll = useCallback(async () => {
    try {
      const data = await api.get("/scan/status");
      if (mounted.current) setStatus(data || {});
      return data || {};
    } catch (e) {
      return null;
    }
  }, []);

  // Schedule polling while any kind is running.
  useEffect(() => {
    mounted.current = true;
    poll();
    return () => {
      mounted.current = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [poll]);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    if (anyRunning) {
      timer.current = setTimeout(() => {
        poll();
      }, 1500);
    }
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [anyRunning, status, poll]);

  async function start(path, label) {
    setStarting(path);
    try {
      await api.post(path);
      t.success(`${label} scan started`);
      await poll();
    } catch (e) {
      t.error(`Scan failed: ${e.message}`);
    } finally {
      setStarting(null);
    }
  }

  return (
    <div className="card mb-3">
      {!compact && (
        <>
          <div className="card-title" style={{ marginBottom: 4 }}>Scan</div>
          <div className="faint mb-3" style={{ fontSize: 12 }}>
            Scan your libraries for new and changed media. Progress updates live.
          </div>
        </>
      )}

      <div className="row-wrap mb-3">
        <button
          className="btn btn-primary btn-sm"
          onClick={() => start("/scan/all", "Full")}
          disabled={!!starting || anyRunning}
        >
          <IconRefresh size={14} className={anyRunning ? "spin" : undefined} /> Scan all
        </button>
        {KINDS.map((k) => (
          <button
            key={k.key}
            className="btn btn-secondary btn-sm"
            onClick={() => start(k.path, k.label)}
            disabled={starting === k.path || (status[k.key] && status[k.key].running)}
          >
            {starting === k.path ? "Starting…" : k.label}
          </button>
        ))}
      </div>

      <div className="col" style={{ gap: 12 }}>
        {KINDS.map((k) => {
          const s = status[k.key];
          if (!s) {
            return (
              <div key={k.key} className="scan-row">
                <div className="scan-row-head">
                  <span className="scan-kind">{k.label}</span>
                  <span className="faint">not scanned yet</span>
                </div>
              </div>
            );
          }
          const p = percent(s);
          return (
            <div key={k.key} className="scan-row">
              <div className="scan-row-head">
                <span className="scan-kind">{k.label}</span>
                <span className="scan-stats">
                  {s.running ? (
                    <span className="badge badge-accent">running…</span>
                  ) : s.error ? (
                    <span className="badge badge-error" title={s.error}>error</span>
                  ) : (
                    <span className="badge badge-ok">done</span>
                  )}
                  <span className="faint" style={{ marginLeft: 8 }}>
                    {s.processed ?? 0}/{s.total ?? 0} · found {s.found ?? 0}
                  </span>
                </span>
              </div>
              <div className="scan-bar-row">
                <div className="progress" style={{ flex: 1 }}>
                  <div
                    className={"progress-fill" + (!s.running && !s.error ? " done" : "")}
                    style={{ width: p + "%" }}
                  />
                </div>
                <span className="scan-pct">{p}%</span>
              </div>
              {s.error && <div className="list-item-sub" style={{ color: "var(--err)" }}>{s.error}</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
