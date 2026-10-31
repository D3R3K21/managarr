import React, { useState } from "react";
import api from "../api/client";
import { useApi } from "../hooks/useApi";
import { useToast } from "../hooks/useToast";
import PageHeader from "../components/PageHeader";
import Switch from "../components/Switch";
import { Loading, ErrorState, EmptyState } from "../components/States";
import { IconPlus, IconTrash } from "../components/icons";

const KINDS = ["movie", "tv", "audiobook"];
const EMPTY = {
  name: "", media_kind: "movie", min_seeders: 1,
  min_size_mb: null, max_size_mb: null,
  allowed_qualities: [], preferred_words: [], avoid_words: [], is_default: false,
};

function csv(arr) { return (arr || []).join(", "); }
function parseCsv(s) { return (s || "").split(",").map((x) => x.trim()).filter(Boolean); }

export default function Quality() {
  const t = useToast();
  const { data, loading, error, refetch } = useApi("/quality");
  const [editing, setEditing] = useState(null);

  async function remove(p) {
    if (!confirm(`Delete profile "${p.name}"?`)) return;
    try {
      await api.del(`/quality/${p.id}`);
      t.success("Deleted");
      refetch();
    } catch (e) {
      t.error(e.message);
    }
  }

  async function save(form) {
    const body = {
      name: form.name,
      media_kind: form.media_kind,
      min_seeders: Number(form.min_seeders) || 0,
      min_size_mb: form.min_size_mb === "" || form.min_size_mb == null ? null : Number(form.min_size_mb),
      max_size_mb: form.max_size_mb === "" || form.max_size_mb == null ? null : Number(form.max_size_mb),
      allowed_qualities: form.allowed_qualities,
      preferred_words: form.preferred_words,
      avoid_words: form.avoid_words,
      is_default: form.is_default,
    };
    try {
      if (form.id) await api.patch(`/quality/${form.id}`, body);
      else await api.post("/quality", body);
      t.success("Saved");
      setEditing(null);
      refetch();
    } catch (e) {
      t.error(e.message);
    }
  }

  return (
    <div>
      <PageHeader
        title="Quality Profiles"
        subtitle="Decision rules for auto-grab (sorted by most seeders)"
        actions={<button className="btn btn-primary btn-sm" onClick={() => setEditing({ ...EMPTY })}><IconPlus size={14} /> Add</button>}
      />

      {editing && <ProfileForm initial={editing} onCancel={() => setEditing(null)} onSave={save} />}

      {loading && <Loading />}
      {error && <ErrorState error={error} onRetry={refetch} />}
      {!loading && !error && (data || []).length === 0 && (
        <EmptyState icon="🎚" title="No profiles" hint="Create one to control auto-grab quality." />
      )}

      {!loading && (data || []).length > 0 && (
        <div className="col">
          {data.map((p) => (
            <div className="card" key={p.id}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div>
                  <div className="card-title" style={{ margin: 0 }}>
                    {p.name}{" "}
                    <span className="badge badge-muted">{p.media_kind}</span>
                    {p.is_default && <span className="badge badge-accent" style={{ marginLeft: 6 }}>default</span>}
                  </div>
                  <div className="muted" style={{ fontSize: 13, marginTop: 6 }}>
                    min {p.min_seeders} seeders
                    {p.min_size_mb != null && ` · ≥${p.min_size_mb}MB`}
                    {p.max_size_mb != null && ` · ≤${p.max_size_mb}MB`}
                  </div>
                  <div className="row-wrap mt-2">
                    {(p.allowed_qualities || []).map((q) => <span key={q} className="badge badge-info">{q}</span>)}
                    {(p.preferred_words || []).map((q) => <span key={q} className="badge badge-ok">+{q}</span>)}
                    {(p.avoid_words || []).map((q) => <span key={q} className="badge badge-error">−{q}</span>)}
                  </div>
                </div>
                <div className="row gap-sm">
                  <button className="btn btn-ghost btn-sm" onClick={() => setEditing(p)}>Edit</button>
                  <button className="btn btn-danger btn-sm" onClick={() => remove(p)}><IconTrash size={13} /></button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ProfileForm({ initial, onCancel, onSave }) {
  const [form, setForm] = useState({
    ...initial,
    allowedText: csv(initial.allowed_qualities),
    preferredText: csv(initial.preferred_words),
    avoidText: csv(initial.avoid_words),
  });
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  function submit(e) {
    e.preventDefault();
    onSave({
      ...form,
      allowed_qualities: parseCsv(form.allowedText),
      preferred_words: parseCsv(form.preferredText),
      avoid_words: parseCsv(form.avoidText),
    });
  }

  return (
    <form className="card mb-3" onSubmit={submit}>
      <div className="card-title">{form.id ? "Edit profile" : "New profile"}</div>
      <div className="grid-2">
        <div className="field">
          <label className="input-label">Name</label>
          <input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} required />
        </div>
        <div className="field">
          <label className="input-label">Media kind</label>
          <select className="select" value={form.media_kind} onChange={(e) => set("media_kind", e.target.value)}>
            {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </div>
      </div>
      <div className="grid-2">
        <div className="field">
          <label className="input-label">Min seeders</label>
          <input className="input" type="number" min="0" value={form.min_seeders} onChange={(e) => set("min_seeders", e.target.value)} />
        </div>
        <div className="field">
          <label className="input-label">Allowed qualities (comma-sep)</label>
          <input className="input" placeholder="1080p, 720p" value={form.allowedText} onChange={(e) => set("allowedText", e.target.value)} />
        </div>
      </div>
      <div className="grid-2">
        <div className="field">
          <label className="input-label">Min size (MB)</label>
          <input className="input" type="number" value={form.min_size_mb ?? ""} onChange={(e) => set("min_size_mb", e.target.value)} />
        </div>
        <div className="field">
          <label className="input-label">Max size (MB)</label>
          <input className="input" type="number" value={form.max_size_mb ?? ""} onChange={(e) => set("max_size_mb", e.target.value)} />
        </div>
      </div>
      <div className="grid-2">
        <div className="field">
          <label className="input-label">Preferred words</label>
          <input className="input" placeholder="x265, HEVC" value={form.preferredText} onChange={(e) => set("preferredText", e.target.value)} />
        </div>
        <div className="field">
          <label className="input-label">Avoid words</label>
          <input className="input" placeholder="CAM, TS" value={form.avoidText} onChange={(e) => set("avoidText", e.target.value)} />
        </div>
      </div>
      <div className="row">
        <Switch checked={form.is_default} onChange={(v) => set("is_default", v)} />
        <span className="muted" style={{ fontSize: 13 }}>Default for this media kind</span>
        <div style={{ flex: 1 }} />
        <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel}>Cancel</button>
        <button type="submit" className="btn btn-primary btn-sm">Save</button>
      </div>
    </form>
  );
}
