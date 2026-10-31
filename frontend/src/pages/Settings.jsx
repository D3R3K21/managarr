import React, { useState } from "react";
import api from "../api/client";
import { useApi } from "../hooks/useApi";
import { useToast } from "../hooks/useToast";
import PageHeader from "../components/PageHeader";
import LibrariesManager from "../components/LibrariesManager";
import DevicesManager from "../components/DevicesManager";
import AuthorizeDevice from "../components/AuthorizeDevice";
import ScanPanel from "../components/ScanPanel";
import UsersManager from "../components/UsersManager";
import { useAuth } from "../context/AuthContext";
import { Loading, ErrorState } from "../components/States";
import { IconPlus, IconRefresh } from "../components/icons";

// A labelled integer setting that commits on blur / Enter (so it doesn't PUT on
// every keystroke). Reverts to the effective value on an invalid/blank entry.
function NumberRow({ label, help, value, min = 1, disabled, onSave }) {
  const [v, setV] = useState(String(value));
  React.useEffect(() => { setV(String(value)); }, [value]);
  const commit = () => {
    const n = parseInt(v, 10);
    if (Number.isFinite(n) && n >= min && String(n) !== String(value)) onSave(n);
    else setV(String(value));
  };
  return (
    <div className="row" style={{ alignItems: "center", gap: 12, marginTop: 10 }}>
      <div className="kv-key" style={{ minWidth: 220 }}>{label}</div>
      <input
        type="number" className="input" style={{ maxWidth: 110 }} min={min}
        value={v} disabled={disabled}
        onChange={(e) => setV(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
      />
      <span className="faint" style={{ fontSize: 13 }}>{help}</span>
    </div>
  );
}

export default function Settings() {
  const t = useToast();
  const { isAdmin } = useAuth();
  const runtime = useApi("/settings");
  const config = useApi("/settings/config");
  const [newKey, setNewKey] = useState("");
  const [newVal, setNewVal] = useState("");
  const [drafts, setDrafts] = useState({});

  async function put(key, value) {
    try {
      await api.put("/settings", { key, value: String(value) });
      t.success(`Saved ${key}`);
      runtime.refetch();
    } catch (e) {
      t.error(e.message);
    }
  }

  async function add(e) {
    e.preventDefault();
    if (!newKey.trim()) return;
    await put(newKey.trim(), newVal);
    setNewKey("");
    setNewVal("");
  }

  const runtimeEntries = runtime.data ? Object.entries(runtime.data) : [];
  const configEntries = config.data ? Object.entries(config.data) : [];

  // Effective download prefs: runtime override wins, else the config default.
  const bool = (rt, cf, dflt) => String(rt ?? cf ?? dflt).toLowerCase() === "true";
  const downloadsEnabled = bool(runtime.data?.DOWNLOADS_ENABLED, config.data?.DOWNLOADS_ENABLED, "true");
  const autoDownloadsEnabled = bool(runtime.data?.AUTO_DOWNLOAD_ENABLED, config.data?.AUTO_DOWNLOAD_ENABLED, "true");
  const downloadPriority =
    runtime.data?.QBT_QUEUE_PRIORITY ?? config.data?.QBT_QUEUE_PRIORITY ?? "none";
  const forceStart = bool(runtime.data?.QBT_FORCE_START, config.data?.QBT_FORCE_START, "false");
  const int = (rt, cf, dflt) => {
    const n = parseInt(rt ?? cf ?? dflt, 10);
    return Number.isFinite(n) ? n : dflt;
  };
  const maxConcurrent = int(runtime.data?.AUTO_DOWNLOAD_MAX_CONCURRENT, config.data?.AUTO_DOWNLOAD_MAX_CONCURRENT, 5);
  const syncInterval = int(runtime.data?.AUTO_DOWNLOAD_SYNC_INTERVAL_MINUTES, config.data?.AUTO_DOWNLOAD_SYNC_INTERVAL_MINUTES, 30);
  const maxAttempts = int(runtime.data?.AUTO_DOWNLOAD_MAX_ATTEMPTS, config.data?.AUTO_DOWNLOAD_MAX_ATTEMPTS, 5);
  const autoEnabled = downloadsEnabled && autoDownloadsEnabled;
  const maxM4b = int(runtime.data?.M4B_MAX_CONCURRENT, config.data?.M4B_MAX_CONCURRENT, 1);

  return (
    <div>
      <PageHeader
        title="Settings"
        subtitle="Runtime overrides and the effective (redacted) config"
        actions={<button className="btn btn-secondary btn-sm" onClick={() => { runtime.refetch(); config.refetch(); }}><IconRefresh size={14} /> Refresh</button>}
      />

      <LibrariesManager />
      <DevicesManager />

      <div className="card mb-3">
        <div className="card-title">Authorize a device</div>
        <div className="faint mb-3" style={{ fontSize: 12 }}>
          Signing in on a TV or another device? Choose “Pair this device” there, then enter
          the code it shows (or scan its QR) to sign it in as you — no password typed on the TV.
        </div>
        <AuthorizeDevice />
      </div>

      <ScanPanel />
      {isAdmin && <UsersManager />}

      <div className="card mb-3">
        <div className="card-title">Downloads</div>

        <div className="row" style={{ alignItems: "center", gap: 12 }}>
          <div className="kv-key" style={{ minWidth: 220 }}>qBittorrent integration</div>
          <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={downloadsEnabled}
              onChange={(e) => put("DOWNLOADS_ENABLED", e.target.checked ? "true" : "false")}
            />
            <span className="faint" style={{ fontSize: 13 }}>
              Master switch — when off, nothing is sent to qBittorrent (manual or automatic)
            </span>
          </label>
        </div>

        <div className="row" style={{ alignItems: "center", gap: 12, marginTop: 10 }}>
          <div className="kv-key" style={{ minWidth: 220 }}>Automatic downloads</div>
          <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: downloadsEnabled ? "pointer" : "not-allowed" }}>
            <input
              type="checkbox"
              checked={downloadsEnabled && autoDownloadsEnabled}
              disabled={!downloadsEnabled}
              onChange={(e) => put("AUTO_DOWNLOAD_ENABLED", e.target.checked ? "true" : "false")}
            />
            <span className="faint" style={{ fontSize: 13 }}>
              Let scheduled jobs grab automatically (RSS re-search, watchlist sync, calendar).
              Turn off to stop auto-downloading “missing” items while keeping manual grabs.
            </span>
          </label>
        </div>

        <div className="row" style={{ alignItems: "center", gap: 12, marginTop: 14 }}>
          <div className="kv-key" style={{ minWidth: 220, fontWeight: 600 }}>Auto-download queue</div>
          <span className="faint" style={{ fontSize: 13 }}>
            Throttles automatic grabs (monitored items + watched collections). Manual grabs bypass these limits.
          </span>
        </div>
        <NumberRow
          label="Max concurrent auto-downloads" min={1}
          value={maxConcurrent} disabled={!autoEnabled}
          onSave={(n) => put("AUTO_DOWNLOAD_MAX_CONCURRENT", n)}
          help="How many auto-downloads are sent to qBittorrent at once. The rest wait in the queue."
        />
        <NumberRow
          label="Sync interval (minutes)" min={1}
          value={syncInterval} disabled={!autoEnabled}
          onSave={(n) => put("AUTO_DOWNLOAD_SYNC_INTERVAL_MINUTES", n)}
          help="How often the queue is topped up from monitored-missing items & watched collections."
        />
        <NumberRow
          label="Search attempts before parking" min={1}
          value={maxAttempts} disabled={!autoEnabled}
          onSave={(n) => put("AUTO_DOWNLOAD_MAX_ATTEMPTS", n)}
          help="After this many searches with no acceptable release, the item is parked as “releaseless.”"
        />

        <hr style={{ border: "none", borderTop: "1px solid var(--border)", margin: "14px 0" }} />

        <div className="row" style={{ alignItems: "center", gap: 12 }}>
          <div className="kv-key" style={{ minWidth: 220 }}>Download priority</div>
          <select
            className="input"
            style={{ maxWidth: 360 }}
            value={downloadPriority}
            onChange={(e) => put("QBT_QUEUE_PRIORITY", e.target.value)}
          >
            <option value="none">Normal — append to bottom (qBittorrent default)</option>
            <option value="top">Top of queue — jump ahead of queued downloads</option>
            <option value="bottom">Bottom of queue — always last</option>
          </select>
        </div>
        <div className="row" style={{ alignItems: "center", gap: 12, marginTop: 10 }}>
          <div className="kv-key" style={{ minWidth: 220 }}>Force start</div>
          <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={forceStart}
              onChange={(e) => put("QBT_FORCE_START", e.target.checked ? "true" : "false")}
            />
            <span className="faint" style={{ fontSize: 13 }}>
              Start immediately, ignoring the queue &amp; active-download limit
            </span>
          </label>
        </div>
        <div className="faint" style={{ fontSize: 12, marginTop: 10 }}>
          Applied to every torrent Managarr sends to qBittorrent (watchlist, missing, manual grabs).
          Priority reorders it within the queue; Force start is independent — combine them to put a
          grab at the top <em>and</em> start it right away regardless of the active-download limit.
        </div>
      </div>

      <div className="card mb-3">
        <div className="card-title">Audiobook conversions</div>
        <NumberRow
          label="Max concurrent m4b conversions" min={1}
          value={maxM4b}
          onSave={(n) => put("M4B_MAX_CONCURRENT", n)}
          help="How many MP3→m4b conversions run at once (ffmpeg-heavy). 1 = strictly one at a time; the rest queue and wait."
        />
      </div>

      <div className="card mb-3">
        <div className="card-title">Runtime overrides</div>
        {runtime.loading && <Loading />}
        {runtime.error && <ErrorState error={runtime.error} onRetry={runtime.refetch} />}

        {!runtime.loading && runtimeEntries.length === 0 && (
          <div className="faint mb-3">No overrides set yet.</div>
        )}

        {runtimeEntries.map(([k, v]) => (
          <div className="row mb-2" key={k}>
            <div className="kv-key" style={{ minWidth: 220 }}>{k}</div>
            <input
              className="input"
              defaultValue={v}
              onChange={(e) => setDrafts((d) => ({ ...d, [k]: e.target.value }))}
            />
            <button className="btn btn-primary btn-sm" onClick={() => put(k, drafts[k] ?? v)}>Save</button>
          </div>
        ))}

        <form className="row mt-3" onSubmit={add}>
          <input className="input" placeholder="KEY" value={newKey} onChange={(e) => setNewKey(e.target.value)} style={{ maxWidth: 220 }} />
          <input className="input" placeholder="value" value={newVal} onChange={(e) => setNewVal(e.target.value)} />
          <button className="btn btn-secondary btn-sm" type="submit"><IconPlus size={14} /> Add</button>
        </form>
      </div>

      <div className="card">
        <div className="card-title">Effective config (secrets masked)</div>
        {config.loading && <Loading />}
        {config.error && <ErrorState error={config.error} onRetry={config.refetch} />}
        {!config.loading && (
          <div>
            {configEntries.map(([k, v]) => {
              const masked = v === "set" || v === "not set";
              return (
                <div className="kv" key={k}>
                  <span className="kv-key">{k}</span>
                  <span className="kv-val">
                    {masked ? (
                      <span className={"badge " + (v === "set" ? "badge-ok" : "badge-muted")}>{v}</span>
                    ) : typeof v === "object" ? (
                      <code style={{ fontSize: 12 }}>{JSON.stringify(v)}</code>
                    ) : (
                      String(v)
                    )}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
