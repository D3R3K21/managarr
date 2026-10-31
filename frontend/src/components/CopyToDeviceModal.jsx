import React, { useEffect, useState, useCallback } from "react";
import api from "../api/client";
import { useToast } from "../hooks/useToast";
import Modal from "./Modal";
import { Loading } from "./States";
import { formatBytes } from "../utils/format";
import { pressable } from "../utils/a11y";
import { IconShield, IconTrash, IconCheck } from "./icons";

// Transfer a movie/episode to an on-network device (Shield's local Plex library).
// Flow: pick device (live free space) → if it wouldn't leave enough headroom, pick
// files to delete on the device → transfer with live progress.
// props: open, onClose, mediaKind ("movie"|"tv"), itemId, title
export default function CopyToDeviceModal({ open, onClose, mediaKind, itemId, title }) {
  const t = useToast();
  const [devices, setDevices] = useState(null);
  const [deviceId, setDeviceId] = useState(null);
  const [plan, setPlan] = useState(null);
  const [planErr, setPlanErr] = useState(null);
  const [toDelete, setToDelete] = useState({});   // path -> size
  const [job, setJob] = useState(null);           // active/finished copy job
  const [busy, setBusy] = useState(false);

  const reset = useCallback(() => {
    setDeviceId(null); setPlan(null); setPlanErr(null); setToDelete({}); setJob(null); setBusy(false);
  }, []);

  // load devices (with live free space) when opened
  useEffect(() => {
    if (!open) return;
    reset();
    setDevices(null);
    api.get("/devices", { space: 1 }).then(setDevices).catch((e) => { setDevices([]); t.error(e.message); });
  }, [open, reset]); // eslint-disable-line react-hooks/exhaustive-deps

  // load plan when a device is selected
  useEffect(() => {
    if (deviceId == null) return;
    setPlan(null); setPlanErr(null); setToDelete({});
    api.get(`/devices/${deviceId}/plan`, { media_kind: mediaKind, item_id: itemId })
      .then(setPlan)
      .catch((e) => setPlanErr(e.message));
  }, [deviceId, mediaKind, itemId]);

  // poll the running job
  useEffect(() => {
    if (!job || job.status === "done" || job.status === "error") return undefined;
    const iv = setInterval(async () => {
      try {
        const jobs = await api.get("/devices/copy-jobs");
        const j = (jobs || []).find((x) => x.id === job.id);
        if (j) {
          setJob(j);
          if (j.status === "done") t.success(`Transferred to ${devName(deviceId)}`);
          if (j.status === "error") t.error(`Transfer failed: ${j.message || "unknown"}`);
        }
      } catch { /* keep polling */ }
    }, 1500);
    return () => clearInterval(iv);
  }, [job, deviceId]); // eslint-disable-line react-hooks/exhaustive-deps

  function devName(id) {
    return (devices || []).find((d) => d.id === id)?.name || "device";
  }

  const selectedSize = Object.values(toDelete).reduce((a, b) => a + b, 0);
  const projFree = plan ? (plan.free_bytes + selectedSize) : 0;
  const fits = plan ? (projFree - plan.media_size) >= plan.headroom_bytes : false;
  // First selectable device — the "obvious" target auto-focused on a TV.
  const firstReachableId = (devices || []).find((d) => d.enabled && d.free_bytes != null)?.id;
  // Which step we're on — drives Modal's TV auto-focus (re-focus on step change).
  // Includes async-load signals (devices / plan) so focus re-runs once the list
  // renders — otherwise it fires before any device exists and lands on Cancel.
  const step = job ? "job"
    : deviceId != null ? (plan ? (plan.needs_room ? "plan-room" : "plan-ok") : "plan-load")
    : (devices == null ? "devices-loading" : `devices-${devices.length}`);
  const impossible = plan && !fits && Object.keys(toDelete).length >= (plan.files?.length || 0)
    && (plan.total_bytes - plan.media_size) < plan.headroom_bytes;

  async function transfer() {
    setBusy(true);
    try {
      const snap = await api.post(`/devices/${deviceId}/copy`, {
        media_kind: mediaKind, item_id: itemId, delete_paths: Object.keys(toDelete),
      });
      setJob({ ...snap, status: "queued", progress: 0 });
    } catch (e) {
      t.error(e.message);
    } finally {
      setBusy(false);
    }
  }

  function toggleDelete(f) {
    setToDelete((prev) => {
      const next = { ...prev };
      if (next[f.path] != null) delete next[f.path];
      else next[f.path] = f.size;
      return next;
    });
  }

  // ── views ───────────────────────────────────────────────────────────────
  let body;
  if (job) {
    const pct = job.progress || 0;
    const done = job.status === "done";
    const err = job.status === "error";
    body = (
      <div>
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
          <strong>{done ? "Transferred" : err ? "Failed" : "Transferring…"}</strong>
          <span className="faint">{devName(deviceId)}</span>
        </div>
        <div className="faint" style={{ fontSize: 12, marginBottom: 10, wordBreak: "break-all" }}>{title}</div>
        {err ? (
          <div className="badge badge-error" style={{ whiteSpace: "normal" }}>{job.message || "unknown error"}</div>
        ) : (
          <>
            <div style={{ height: 10, background: "var(--surface-2, #222)", borderRadius: 6, overflow: "hidden" }}>
              <div style={{ width: `${pct}%`, height: "100%", background: done ? "var(--ok, #3ba55d)" : "var(--accent, #5865f2)", transition: "width .3s" }} />
            </div>
            <div className="faint" style={{ fontSize: 12, marginTop: 6 }}>
              {pct}% · {formatBytes(job.copied_bytes || 0)} / {formatBytes(job.total_bytes || 0)}
              {done && " · done"}
            </div>
          </>
        )}
      </div>
    );
  } else if (deviceId != null) {
    body = (
      <div>
        <button className="btn btn-ghost btn-xs" onClick={() => setDeviceId(null)}>← devices</button>
        {planErr && <div className="badge badge-error" style={{ marginTop: 8, whiteSpace: "normal" }}>{planErr}</div>}
        {!plan && !planErr && <Loading />}
        {plan && (
          <>
            <div style={{ margin: "10px 0" }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span>Transfer</span><strong>{formatBytes(plan.media_size)}</strong>
              </div>
              <div className="faint" style={{ fontSize: 12, wordBreak: "break-all" }}>{plan.filename}</div>
            </div>
            <SpaceBar free={projFree} total={plan.total_bytes} media={plan.media_size} />
            <div className="faint" style={{ fontSize: 12, margin: "6px 0 4px" }}>
              {formatBytes(projFree)} free of {formatBytes(plan.total_bytes)}
              {selectedSize > 0 && ` (after removing ${formatBytes(selectedSize)})`}
              {" · "}leaves {formatBytes(Math.max(0, projFree - plan.media_size))} headroom
            </div>

            {(plan.needs_room || selectedSize > 0) && (
              <div className="detail-section" style={{ marginTop: 10 }}>
                <div className="section-title">
                  {fits ? "Freeing enough space ✓" : "Not enough room — pick files to remove from the device"}
                </div>
                {(plan.files || []).length === 0 && <div className="faint">Nothing on the device to remove.</div>}
                <div className="list" style={{ maxHeight: 220, overflowY: "auto" }}>
                  {(plan.files || []).map((f, fi) => {
                    const on = toDelete[f.path] != null;
                    return (
                      <div className={"list-item" + (on ? " highlight" : "")} key={f.path}
                           style={{ cursor: "pointer" }}
                           data-autofocus={fi === 0 ? "" : undefined}
                           {...pressable(() => toggleDelete(f))}>
                        <span style={{ width: 18 }}>{on ? <IconTrash size={14} /> : <span />}</span>
                        <div className="list-item-main">
                          <div className="list-item-title" title={f.path}>{f.name}</div>
                        </div>
                        <span className="faint" style={{ fontSize: 12 }}>{formatBytes(f.size)}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
            {impossible && (
              <div className="badge badge-error" style={{ marginTop: 8, whiteSpace: "normal" }}>
                Even cleared, this device can't hold the file with headroom.
              </div>
            )}
          </>
        )}
      </div>
    );
  } else {
    body = (
      <div>
        {devices == null && <Loading />}
        {devices && devices.length === 0 && (
          <div className="faint">No devices configured. Add one in Settings → Devices.</div>
        )}
        <div className="list">
          {(devices || []).map((d) => {
            const reachable = d.enabled && d.free_bytes != null;
            return (
              <div className={"list-item" + (reachable ? "" : " ")} key={d.id}
                   style={{ cursor: reachable ? "pointer" : "not-allowed", opacity: reachable ? 1 : 0.55 }}
                   data-autofocus={d.id === firstReachableId ? "" : undefined}
                   {...pressable(() => setDeviceId(d.id), { disabled: !reachable })}>
                <IconShield size={16} />
                <div className="list-item-main">
                  <div className="list-item-title">{d.name}{d.location && <span className="faint"> · {d.location}</span>}</div>
                  <div className="list-item-sub faint" style={{ fontSize: 12 }}>
                    {!d.enabled ? "disabled"
                      : d.free_bytes != null ? `${formatBytes(d.free_bytes)} free of ${formatBytes(d.total_bytes)}`
                      : (d.space_error ? "unreachable" : "…")}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  const footer = job ? (
    <button className="btn btn-primary btn-sm" onClick={onClose}>Close</button>
  ) : deviceId != null && plan ? (
    <>
      <button className="btn btn-ghost btn-sm" onClick={onClose}>Cancel</button>
      <button className="btn btn-primary btn-sm" onClick={transfer} disabled={busy || !fits}>
        <IconCheck size={14} /> {selectedSize > 0 ? "Remove & transfer" : "Transfer"}
      </button>
    </>
  ) : (
    <button className="btn btn-ghost btn-sm" onClick={onClose}>Cancel</button>
  );

  return (
    <Modal open={open} onClose={onClose} title="Copy to device" footer={footer} width={560} focusDep={step}>
      {body}
    </Modal>
  );
}

function SpaceBar({ free, total, media }) {
  const used = Math.max(0, total - free);
  const usedPct = total ? (used / total) * 100 : 0;
  const mediaPct = total ? Math.min(100 - usedPct, (media / total) * 100) : 0;
  return (
    <div style={{ height: 10, background: "var(--ok,#3ba55d)", borderRadius: 6, overflow: "hidden", display: "flex" }}>
      <div style={{ width: `${usedPct}%`, background: "var(--surface-3,#4b4b4b)" }} />
      <div style={{ width: `${mediaPct}%`, background: "var(--accent,#5865f2)" }} title="this transfer" />
    </div>
  );
}
