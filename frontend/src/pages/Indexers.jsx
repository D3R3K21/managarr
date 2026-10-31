import React, { useState } from "react";
import api from "../api/client";
import { useApi } from "../hooks/useApi";
import { useToast } from "../hooks/useToast";
import PageHeader from "../components/PageHeader";
import Switch from "../components/Switch";
import ReleaseSearch from "../components/ReleaseSearch";
import { Loading, ErrorState, EmptyState } from "../components/States";
import { IconPlus, IconTrash, IconCheck, IconX } from "../components/icons";

const KINDS = ["iptorrents", "mam"];
const EMPTY = { name: "", kind: "iptorrents", enabled: true, config: {} };

export default function Indexers() {
  const t = useToast();
  const { data, loading, error, refetch, setData } = useApi("/indexers");
  const [editing, setEditing] = useState(null); // indexer obj or EMPTY for new
  const [testResult, setTestResult] = useState({});
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchKind, setSearchKind] = useState("movie");

  async function toggle(ix) {
    try {
      await api.patch(`/indexers/${ix.id}`, { enabled: !ix.enabled });
      setData((rows) => rows.map((r) => (r.id === ix.id ? { ...r, enabled: !ix.enabled } : r)));
    } catch (e) {
      t.error(e.message);
    }
  }

  async function remove(ix) {
    if (!confirm(`Delete indexer "${ix.name}"?`)) return;
    try {
      await api.del(`/indexers/${ix.id}`);
      t.success("Deleted");
      refetch();
    } catch (e) {
      t.error(e.message);
    }
  }

  async function test(ix) {
    setTestResult((r) => ({ ...r, [ix.id]: { loading: true } }));
    try {
      const res = await api.post(`/indexers/test/${ix.id}`);
      setTestResult((r) => ({ ...r, [ix.id]: res }));
    } catch (e) {
      setTestResult((r) => ({ ...r, [ix.id]: { ok: false, error: e.message } }));
    }
  }

  async function save(form) {
    try {
      if (form.id) await api.patch(`/indexers/${form.id}`, form);
      else await api.post("/indexers", form);
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
        title="Indexers"
        subtitle="Torrent indexers used for release search"
        actions={
          <>
            <button className="btn btn-secondary btn-sm" onClick={() => setSearchOpen((o) => !o)}>Interactive search</button>
            <button className="btn btn-primary btn-sm" onClick={() => setEditing({ ...EMPTY })}><IconPlus size={14} /> Add</button>
          </>
        }
      />

      {searchOpen && (
        <div className="card mb-3">
          <div className="row mb-2">
            <span className="card-title" style={{ margin: 0 }}>Global search</span>
            {["movie", "tv", "audiobook"].map((k) => (
              <button key={k} className={"chip" + (searchKind === k ? " active" : "")} onClick={() => setSearchKind(k)}>{k}</button>
            ))}
          </div>
          <ReleaseSearch mediaKind={searchKind} />
        </div>
      )}

      {editing && <IndexerForm initial={editing} onCancel={() => setEditing(null)} onSave={save} />}

      {loading && <Loading />}
      {error && <ErrorState error={error} onRetry={refetch} />}
      {!loading && !error && (data || []).length === 0 && (
        <EmptyState icon="🔌" title="No indexers" hint="Add one to start searching." />
      )}

      {!loading && (data || []).length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr><th>Name</th><th>Kind</th><th>Enabled</th><th>Test</th><th></th></tr>
            </thead>
            <tbody>
              {data.map((ix) => {
                const tr = testResult[ix.id];
                return (
                  <tr key={ix.id}>
                    <td style={{ fontWeight: 600 }}>{ix.name}</td>
                    <td><span className="badge badge-muted">{ix.kind}</span></td>
                    <td><Switch checked={ix.enabled} onChange={() => toggle(ix)} /></td>
                    <td>
                      <button className="btn btn-ghost btn-xs" onClick={() => test(ix)} disabled={tr?.loading}>
                        {tr?.loading ? "…" : "Test"}
                      </button>
                      {tr && !tr.loading && (
                        tr.ok ? (
                          <span className="badge badge-ok" style={{ marginLeft: 6 }}><IconCheck size={11} /> {tr.results} hits</span>
                        ) : (
                          <span className="badge badge-error" style={{ marginLeft: 6 }} title={tr.error}><IconX size={11} /> fail</span>
                        )
                      )}
                    </td>
                    <td className="row gap-sm">
                      <button className="btn btn-ghost btn-xs" onClick={() => setEditing(ix)}>Edit</button>
                      <button className="btn btn-danger btn-xs" onClick={() => remove(ix)}><IconTrash size={12} /></button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function IndexerForm({ initial, onCancel, onSave }) {
  const [form, setForm] = useState({
    ...initial,
    configText: JSON.stringify(initial.config || {}, null, 2),
  });
  const [jsonErr, setJsonErr] = useState("");

  function submit(e) {
    e.preventDefault();
    let config = {};
    try {
      config = form.configText.trim() ? JSON.parse(form.configText) : {};
    } catch {
      setJsonErr("Config must be valid JSON");
      return;
    }
    const { configText, ...rest } = form;
    onSave({ ...rest, config });
  }

  return (
    <form className="card mb-3" onSubmit={submit}>
      <div className="card-title">{form.id ? "Edit indexer" : "New indexer"}</div>
      <div className="grid-2">
        <div className="field">
          <label className="input-label">Name</label>
          <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
        </div>
        <div className="field">
          <label className="input-label">Kind</label>
          <select className="select" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
            {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </div>
      </div>
      <div className="field">
        <label className="input-label">Config (JSON)</label>
        <textarea className="textarea code-area" style={{ minHeight: 120 }} value={form.configText}
          onChange={(e) => { setForm({ ...form, configText: e.target.value }); setJsonErr(""); }} />
        {jsonErr && <div style={{ color: "var(--err)", fontSize: 12, marginTop: 4 }}>{jsonErr}</div>}
      </div>
      <div className="row">
        <Switch checked={form.enabled} onChange={(v) => setForm({ ...form, enabled: v })} />
        <span className="muted" style={{ fontSize: 13 }}>Enabled</span>
        <div style={{ flex: 1 }} />
        <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel}>Cancel</button>
        <button type="submit" className="btn btn-primary btn-sm">Save</button>
      </div>
    </form>
  );
}
