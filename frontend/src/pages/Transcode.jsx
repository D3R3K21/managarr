import React, { useState, useEffect } from "react";
import api from "../api/client";
import { useApi } from "../hooks/useApi";
import { useToast } from "../hooks/useToast";
import PageHeader from "../components/PageHeader";
import { Loading, ErrorState, EmptyState } from "../components/States";
import { formatBytes } from "../utils/format";
import { IconRefresh, IconCheck, IconX, IconPlay } from "../components/icons";

export default function Transcode() {
  const t = useToast();
  const candidates = useApi("/transcode/candidates");
  const queue = useApi("/transcode/queue");
  const [scanning, setScanning] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [busy, setBusy] = useState(null);

  // Refresh running queue periodically.
  useEffect(() => {
    const id = setInterval(() => queue.refetch(), 5000);
    return () => clearInterval(id);
  }, [queue.refetch]);

  async function scan() {
    setScanning(true);
    try {
      const r = await api.post("/transcode/scan");
      t.success(`Scan done${r.found != null ? `: ${r.found} candidates` : ""}`);
      candidates.refetch();
    } catch (e) {
      t.error(e.message);
    } finally {
      setScanning(false);
    }
  }

  async function process() {
    setProcessing(true);
    try {
      await api.post("/transcode/process");
      t.success("Processing approved jobs");
      queue.refetch();
    } catch (e) {
      t.error(e.message);
    } finally {
      setProcessing(false);
    }
  }

  async function act(job, action) {
    setBusy(job.id);
    try {
      await api.post(`/transcode/${action}/${job.id}`);
      t.success(action === "approve" ? "Approved" : "Skipped");
      candidates.refetch();
      queue.refetch();
    } catch (e) {
      t.error(e.message);
    } finally {
      setBusy(null);
    }
  }

  const cand = candidates.data || [];
  const jobs = (queue.data || []).filter((j) => j.status !== "candidate");

  return (
    <div>
      <PageHeader
        title="Transcode"
        subtitle="HEVC re-encode candidates and the running queue"
        actions={
          <>
            <button className="btn btn-secondary btn-sm" onClick={scan} disabled={scanning}>
              <IconRefresh size={14} /> {scanning ? "Scanning…" : "Scan"}
            </button>
            <button className="btn btn-primary btn-sm" onClick={process} disabled={processing}>
              <IconPlay size={14} /> {processing ? "…" : "Process queue"}
            </button>
          </>
        }
      />

      <div className="section-title">Candidates ({cand.length})</div>
      {candidates.loading && <Loading />}
      {candidates.error && <ErrorState error={candidates.error} onRetry={candidates.refetch} />}
      {!candidates.loading && cand.length === 0 && (
        <EmptyState icon="🎞" title="No candidates" hint="Scan the library to flag large files." />
      )}
      {cand.length > 0 && (
        <div className="table-wrap mb-3">
          <table className="table">
            <thead>
              <tr><th>Source</th><th>Reason</th><th>Size</th><th>Target</th><th></th></tr>
            </thead>
            <tbody>
              {cand.map((j) => (
                <tr key={j.id}>
                  <td style={{ maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={j.source_path}>
                    {j.source_path}
                  </td>
                  <td className="muted">{j.reason || "—"}</td>
                  <td>{formatBytes(j.source_size_bytes)}</td>
                  <td><span className="badge badge-muted">{j.target_codec}</span></td>
                  <td className="row gap-sm">
                    <button className="btn btn-success btn-xs" onClick={() => act(j, "approve")} disabled={busy === j.id}>
                      <IconCheck size={12} /> Approve
                    </button>
                    <button className="btn btn-ghost btn-xs" onClick={() => act(j, "skip")} disabled={busy === j.id}>
                      <IconX size={12} /> Skip
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="section-title mt-3">Queue ({jobs.length})</div>
      {queue.loading && !queue.data && <Loading />}
      {!queue.loading && jobs.length === 0 && (
        <EmptyState icon="⚙️" title="Queue empty" hint="Approved jobs appear here while encoding." />
      )}
      {jobs.length > 0 && (
        <div className="list">
          {jobs.map((j) => {
            const ratio = j.output_size_bytes && j.source_size_bytes
              ? Math.round((1 - j.output_size_bytes / j.source_size_bytes) * 100) : null;
            return (
              <div className="list-item" key={j.id}>
                <span className={"badge " + statusBadge(j.status)}>{j.status}</span>
                <div className="list-item-main">
                  <div className="list-item-title">{j.source_path}</div>
                  <div className="list-item-sub">
                    {formatBytes(j.source_size_bytes)}
                    {j.output_size_bytes ? ` → ${formatBytes(j.output_size_bytes)}` : ""}
                    {ratio != null ? ` (−${ratio}%)` : ""}
                    {j.message ? ` · ${j.message}` : ""}
                  </div>
                </div>
                {j.status === "running" && (
                  <div style={{ width: 120 }}>
                    <div className="progress"><div className="progress-fill" style={{ width: "60%", animation: "none" }} /></div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function statusBadge(s) {
  if (s === "done") return "badge-ok";
  if (s === "error") return "badge-error";
  if (s === "running") return "badge-info";
  if (s === "skipped") return "badge-muted";
  return "badge-accent";
}
