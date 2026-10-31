import React, { useState } from "react";
import api from "../api/client";
import { useApi } from "../hooks/useApi";
import { useToast } from "../hooks/useToast";
import Modal from "./Modal";
import { Loading, ErrorState } from "./States";
import { formatBytes } from "../utils/format";
import { IconPlus, IconTrash, IconShield } from "./icons";

const EMPTY = {
  name: "", location: "", host: "", share: "internal",
  movies_path: "Movies", tv_path: "TV Shows",
  username: "", password: "", domain: "WORKGROUP", enabled: true,
};

// Manage on-network copy targets (a Shield's local Plex library over SMB).
export default function DevicesManager() {
  const t = useToast();
  const { data, loading, error, refetch } = useApi("/devices", { space: 1 });
  const [editing, setEditing] = useState(null);   // {…device} or EMPTY (new)
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const devices = data || [];
  const isNew = editing && !editing.id;

  async function save() {
    if (!editing.name || !editing.host) { t.error("Name and host are required"); return; }
    setSaving(true);
    try {
      if (isNew) {
        await api.post("/devices", editing);
        t.success(`Added ${editing.name}`);
      } else {
        const body = { ...editing };
        if (!body.password) delete body.password;   // blank => keep existing
        await api.patch(`/devices/${editing.id}`, body);
        t.success(`Saved ${editing.name}`);
      }
      setEditing(null);
      refetch();
    } catch (e) { t.error(e.message); } finally { setSaving(false); }
  }

  async function test(d) {
    setBusyId(d.id);
    try {
      const r = await api.post(`/devices/${d.id}/test`);
      if (r.ok) t.success(`${d.name}: ${formatBytes(r.free_bytes)} free of ${formatBytes(r.total_bytes)}`);
      else t.error(`${d.name}: ${r.error}`);
    } catch (e) { t.error(e.message); } finally { setBusyId(null); }
  }

  async function remove(d) {
    if (!window.confirm(`Remove device “${d.name}”?`)) return;
    setBusyId(d.id);
    try {
      await api.del(`/devices/${d.id}`);
      t.success(`Removed ${d.name}`);
      refetch();
    } catch (e) { t.error(e.message); } finally { setBusyId(null); }
  }

  function set(k, v) { setEditing((e) => ({ ...e, [k]: v })); }

  return (
    <div className="card mb-3">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <div className="card-title" style={{ marginBottom: 0 }}>Devices</div>
        <button className="btn btn-primary btn-sm" onClick={() => setEditing({ ...EMPTY })}>
          <IconPlus size={14} /> Add device
        </button>
      </div>
      <div className="faint mb-3" style={{ fontSize: 12 }}>
        On-network devices you can copy media onto over SMB (e.g. a Shield's local Plex library).
      </div>

      {loading && <Loading />}
      {error && <ErrorState error={error} onRetry={refetch} />}

      {!loading && devices.length === 0 && (
        <div className="faint mb-2">No devices. Click “Add device”.</div>
      )}

      {devices.length > 0 && (
        <div className="list">
          {devices.map((d) => (
            <div className="list-item" key={d.id}>
              <IconShield size={16} />
              <div className="list-item-main">
                <div className="list-item-title">
                  {d.name}{d.location && <span className="faint"> · {d.location}</span>}
                  {!d.enabled && <span className="badge badge-muted" style={{ marginLeft: 8 }}>disabled</span>}
                </div>
                <div className="list-item-sub faint" style={{ fontSize: 12 }}>
                  {d.host}/{d.share}
                  {d.free_bytes != null ? ` · ${formatBytes(d.free_bytes)} free of ${formatBytes(d.total_bytes)}`
                    : d.space_error ? " · unreachable" : ""}
                </div>
              </div>
              <button className="btn btn-secondary btn-sm" disabled={busyId === d.id} onClick={() => test(d)}>Test</button>
              <button className="btn btn-ghost btn-sm" disabled={busyId === d.id} onClick={() => setEditing({ ...d, password: "" })}>Edit</button>
              <button className="btn btn-danger btn-sm" disabled={busyId === d.id} onClick={() => remove(d)} title="Remove">
                <IconTrash size={14} />
              </button>
            </div>
          ))}
        </div>
      )}

      <Modal open={!!editing} onClose={() => setEditing(null)}
             title={isNew ? "Add device" : `Edit ${editing?.name || "device"}`}
             footer={
               <>
                 <button className="btn btn-ghost btn-sm" onClick={() => setEditing(null)}>Cancel</button>
                 <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>{saving ? "…" : "Save"}</button>
               </>
             }>
        {editing && (
          <div className="form-grid" style={{ display: "grid", gap: 10 }}>
            <Field label="Name"><input className="input" value={editing.name} onChange={(e) => set("name", e.target.value)} placeholder="Living room Shield" /></Field>
            <Field label="Location / room"><input className="input" value={editing.location || ""} onChange={(e) => set("location", e.target.value)} placeholder="Living room" /></Field>
            <div className="row gap-sm">
              <Field label="Host / IP" grow><input className="input" value={editing.host} onChange={(e) => set("host", e.target.value)} placeholder="192.168.1.50" /></Field>
              <Field label="SMB share"><input className="input" value={editing.share} onChange={(e) => set("share", e.target.value)} placeholder="internal" /></Field>
            </div>
            <div className="row gap-sm">
              <Field label="Movies subfolder" grow><input className="input" value={editing.movies_path} onChange={(e) => set("movies_path", e.target.value)} placeholder="Movies" /></Field>
              <Field label="TV subfolder" grow><input className="input" value={editing.tv_path} onChange={(e) => set("tv_path", e.target.value)} placeholder="TV Shows" /></Field>
            </div>
            <div className="row gap-sm">
              <Field label="Username" grow><input className="input" value={editing.username} onChange={(e) => set("username", e.target.value)} autoComplete="off" /></Field>
              <Field label="Domain"><input className="input" value={editing.domain} onChange={(e) => set("domain", e.target.value)} placeholder="WORKGROUP" /></Field>
            </div>
            <Field label={isNew ? "Password" : "Password (blank = unchanged)"}>
              <input className="input" type="password" value={editing.password || ""} onChange={(e) => set("password", e.target.value)}
                     autoComplete="new-password" placeholder={!isNew && editing.has_password ? "••••••••" : ""} />
            </Field>
            <label className="row gap-sm" style={{ alignItems: "center", cursor: "pointer" }}>
              <input type="checkbox" checked={!!editing.enabled} onChange={(e) => set("enabled", e.target.checked)} /> Enabled
            </label>
          </div>
        )}
      </Modal>
    </div>
  );
}

function Field({ label, children, grow }) {
  return (
    <label style={{ display: "block", flex: grow ? 1 : "initial" }}>
      <div className="faint" style={{ fontSize: 12, marginBottom: 4 }}>{label}</div>
      {children}
    </label>
  );
}
