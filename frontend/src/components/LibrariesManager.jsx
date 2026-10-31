import React, { useState } from "react";
import api from "../api/client";
import { useApi } from "../hooks/useApi";
import { useToast } from "../hooks/useToast";
import Modal from "./Modal";
import DirectoryPicker from "./DirectoryPicker";
import { Loading, ErrorState } from "./States";
import { IconPlus, IconTrash, IconLibrary } from "./icons";

const MEDIA_KINDS = [
  { value: "movie", label: "Movies" },
  { value: "tv", label: "TV" },
  { value: "audiobook", label: "Audiobooks" },
];

function kindLabel(v) {
  const k = MEDIA_KINDS.find((m) => m.value === v);
  return k ? k.label : v;
}

export default function LibrariesManager() {
  const t = useToast();
  const { data, loading, error, refetch } = useApi("/library/rootfolders");
  const [adding, setAdding] = useState(false);
  const [pickerPath, setPickerPath] = useState("/");
  const [pickKind, setPickKind] = useState("movie");
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState(null);

  const folders = data || [];

  function openAdd() {
    setPickerPath("/");
    setPickKind("movie");
    setAdding(true);
  }

  async function save() {
    if (!pickerPath) return;
    setSaving(true);
    try {
      await api.post("/library/rootfolders", { path: pickerPath, media_kind: pickKind });
      t.success(`Added ${kindLabel(pickKind)} library: ${pickerPath}`);
      setAdding(false);
      refetch();
    } catch (e) {
      t.error(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function changeKind(folder, media_kind) {
    if (media_kind === folder.media_kind) return;
    setBusyId(folder.id);
    try {
      // No PATCH endpoint for root folders — re-create with the new kind.
      await api.post("/library/rootfolders", { path: folder.path, media_kind });
      await api.del(`/library/rootfolders/${folder.id}`);
      t.success(`Set ${folder.path} → ${kindLabel(media_kind)}`);
      refetch();
    } catch (e) {
      t.error(e.message);
      refetch();
    } finally {
      setBusyId(null);
    }
  }

  async function remove(folder) {
    setBusyId(folder.id);
    try {
      await api.del(`/library/rootfolders/${folder.id}`);
      t.success(`Removed ${folder.path}`);
      refetch();
    } catch (e) {
      t.error(e.message);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="card mb-3">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <div className="card-title" style={{ marginBottom: 0 }}>Libraries</div>
        <button className="btn btn-primary btn-sm" onClick={openAdd}>
          <IconPlus size={14} /> Add library
        </button>
      </div>
      <div className="faint mb-3" style={{ fontSize: 12 }}>
        Root folders scanned for media. You can add multiple folders per type.
      </div>

      {loading && <Loading />}
      {error && <ErrorState error={error} onRetry={refetch} />}

      {!loading && !error && folders.length === 0 && (
        <div className="faint mb-2">No libraries configured. Click “Add library” to pick a folder.</div>
      )}

      {!loading && folders.length > 0 && (
        <div className="list">
          {folders.map((f) => (
            <div className={"list-item" + (f.exists === false ? " highlight" : "")} key={f.id}>
              <IconLibrary size={16} />
              <div className="list-item-main">
                <div className="list-item-title" title={f.path}>{f.path}</div>
                {f.exists === false && (
                  <div className="list-item-sub" style={{ color: "var(--err)" }}>
                    Folder not found on disk
                  </div>
                )}
              </div>
              <select
                className="select"
                style={{ width: 140 }}
                value={f.media_kind}
                disabled={busyId === f.id}
                onChange={(e) => changeKind(f, e.target.value)}
              >
                {MEDIA_KINDS.map((m) => (
                  <option key={m.value} value={m.value}>{m.label}</option>
                ))}
              </select>
              <button
                className="btn btn-danger btn-sm"
                onClick={() => remove(f)}
                disabled={busyId === f.id}
                title="Remove library"
              >
                <IconTrash size={14} />
              </button>
            </div>
          ))}
        </div>
      )}

      <Modal
        open={adding}
        onClose={() => setAdding(false)}
        title="Add library"
        width={620}
        footer={
          <>
            <button className="btn btn-ghost btn-sm" onClick={() => setAdding(false)} disabled={saving}>
              Cancel
            </button>
            <button className="btn btn-primary btn-sm" onClick={save} disabled={saving || !pickerPath}>
              {saving ? "Adding…" : "Select this folder"}
            </button>
          </>
        }
      >
        <div className="field">
          <label className="input-label">Media type</label>
          <select
            className="select"
            value={pickKind}
            onChange={(e) => setPickKind(e.target.value)}
            style={{ maxWidth: 200 }}
          >
            {MEDIA_KINDS.map((m) => (
              <option key={m.value} value={m.value}>{m.label}</option>
            ))}
          </select>
        </div>
        <label className="input-label">Folder</label>
        <DirectoryPicker initialPath="/" onChange={setPickerPath} />
      </Modal>
    </div>
  );
}
