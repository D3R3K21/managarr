import React, { useState } from "react";
import api from "../api/client";
import { useApi } from "../hooks/useApi";
import { useToast } from "../hooks/useToast";
import PageHeader from "../components/PageHeader";
import Switch from "../components/Switch";
import { Loading, ErrorState, EmptyState } from "../components/States";
import { timeAgo } from "../utils/format";
import { IconPlus, IconTrash, IconPlay } from "../components/icons";
import Modal from "../components/Modal";

export default function Scripts() {
  const [tab, setTab] = useState("scripts"); // scripts | tasks
  return (
    <div>
      <PageHeader title="Scripts &amp; Tasks" subtitle="Custom scripts and scheduled actions" />
      <div className="filter-row">
        {[["scripts", "Scripts"], ["tasks", "Scheduled Tasks"]].map(([k, label]) => (
          <button key={k} className={"chip" + (tab === k ? " active" : "")} onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>
      {tab === "scripts" ? <ScriptsView /> : <TasksView />}
    </div>
  );
}

const EMPTY_SCRIPT = { name: "", language: "python", body: "", enabled: true };

function ScriptsView() {
  const t = useToast();
  const { data, loading, error, refetch } = useApi("/scripts");
  const [editing, setEditing] = useState(null);
  const [runs, setRuns] = useState({}); // scriptId -> last run

  async function save(form) {
    try {
      if (form.id) await api.put(`/scripts/${form.id}`, { name: form.name, language: form.language, body: form.body, enabled: form.enabled });
      else await api.post("/scripts", { name: form.name, language: form.language, body: form.body, enabled: form.enabled });
      t.success("Saved");
      setEditing(null);
      refetch();
    } catch (e) {
      t.error(e.message);
    }
  }

  async function remove(s) {
    if (!confirm(`Delete script "${s.name}"?`)) return;
    try {
      await api.del(`/scripts/${s.id}`);
      t.success("Deleted");
      refetch();
    } catch (e) {
      t.error(e.message);
    }
  }

  async function run(s) {
    setRuns((r) => ({ ...r, [s.id]: { loading: true } }));
    try {
      const res = await api.post(`/scripts/${s.id}/run`);
      setRuns((r) => ({ ...r, [s.id]: res }));
      t[res.status === "error" ? "error" : "success"](`Run ${res.status}${res.exit_code != null ? ` (exit ${res.exit_code})` : ""}`);
    } catch (e) {
      setRuns((r) => ({ ...r, [s.id]: { status: "error", stderr: e.message } }));
      t.error(e.message);
    }
  }

  return (
    <div>
      <div className="row mb-3" style={{ justifyContent: "flex-end" }}>
        <button className="btn btn-primary btn-sm" onClick={() => setEditing({ ...EMPTY_SCRIPT })}><IconPlus size={14} /> New script</button>
      </div>

      {editing && <ScriptForm initial={editing} onCancel={() => setEditing(null)} onSave={save} />}

      {loading && <Loading />}
      {error && <ErrorState error={error} onRetry={refetch} />}
      {!loading && !error && (data || []).length === 0 && (
        <EmptyState icon="📜" title="No scripts" hint="Add a Python or Bash script." />
      )}

      <div className="col">
        {(data || []).map((s) => {
          const lr = runs[s.id];
          return (
            <div className="card" key={s.id}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div>
                  <div className="card-title" style={{ margin: 0 }}>
                    {s.name} <span className="badge badge-muted">{s.language}</span>
                    {!s.enabled && <span className="badge badge-warn" style={{ marginLeft: 6 }}>disabled</span>}
                  </div>
                  <div className="faint" style={{ fontSize: 12, marginTop: 4 }}>created {timeAgo(s.created_at)}</div>
                </div>
                <div className="row gap-sm">
                  <button className="btn btn-success btn-sm" onClick={() => run(s)} disabled={lr?.loading}>
                    <IconPlay size={13} /> {lr?.loading ? "…" : "Run"}
                  </button>
                  <button className="btn btn-ghost btn-sm" onClick={() => setEditing(s)}>Edit</button>
                  <button className="btn btn-danger btn-sm" onClick={() => remove(s)}><IconTrash size={13} /></button>
                </div>
              </div>
              {lr && !lr.loading && (lr.stdout || lr.stderr) && (
                <div className="mt-3">
                  {lr.stdout && (
                    <>
                      <div className="section-title">stdout</div>
                      <pre className="chat-result">{lr.stdout}</pre>
                    </>
                  )}
                  {lr.stderr && (
                    <>
                      <div className="section-title" style={{ color: "var(--err)" }}>stderr</div>
                      <pre className="chat-result" style={{ borderColor: "#5e1f1f" }}>{lr.stderr}</pre>
                    </>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ScriptForm({ initial, onCancel, onSave }) {
  const [form, setForm] = useState({ ...initial });
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  function submit(e) { e.preventDefault(); onSave(form); }
  return (
    <form className="card mb-3" onSubmit={submit}>
      <div className="card-title">{form.id ? "Edit script" : "New script"}</div>
      <div className="grid-2">
        <div className="field">
          <label className="input-label">Name</label>
          <input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} required />
        </div>
        <div className="field">
          <label className="input-label">Language</label>
          <select className="select" value={form.language} onChange={(e) => set("language", e.target.value)}>
            <option value="python">python</option>
            <option value="bash">bash</option>
          </select>
        </div>
      </div>
      <div className="field">
        <label className="input-label">Body</label>
        <textarea className="textarea code-area" value={form.body} onChange={(e) => set("body", e.target.value)}
          placeholder={form.language === "bash" ? "#!/usr/bin/env bash\necho hello" : "print('hello')"} />
      </div>
      <div className="row">
        <Switch checked={form.enabled} onChange={(v) => set("enabled", v)} />
        <span className="muted" style={{ fontSize: 13 }}>Enabled</span>
        <div style={{ flex: 1 }} />
        <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel}>Cancel</button>
        <button type="submit" className="btn btn-primary btn-sm">Save</button>
      </div>
    </form>
  );
}

const EMPTY_TASK = { name: "", cron: "0 3 * * *", action: "", script_id: null, enabled: true };

const STATUS_BADGE = { success: "badge-ok", failed: "badge-error", skipped: "badge-warn", running: "badge-info" };

function StatusBadge({ status }) {
  if (!status) return <span className="faint">never run</span>;
  return <span className={"badge " + (STATUS_BADGE[status] || "badge-muted")}>{status}</span>;
}

function fmtDuration(s) {
  if (s == null) return "";
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
}

function Summary({ text }) {
  if (!text) return <span className="faint">—</span>;
  return <div style={{ whiteSpace: "pre-wrap", fontSize: 12, maxWidth: 520 }}>{text}</div>;
}

// Run history for one job from the scheduled-jobs registry.
function HistoryModal({ job, onClose }) {
  const { data, loading, error, refetch } = useApi(job ? `/scripts/jobs/registry/${job.id}/runs` : null);
  return (
    <Modal open={!!job} onClose={onClose} title={job ? `History · ${job.title}` : ""} width={760}>
      {job?.description && <div className="faint" style={{ fontSize: 12, marginBottom: 10 }}>{job.description}</div>}
      {job?.record === "changes" && (
        <div className="faint" style={{ fontSize: 12, marginBottom: 10 }}>
          This job runs often, so only failures and status changes are kept. Last run {timeAgo(job.last_run_at)}.
        </div>
      )}
      {loading && <Loading />}
      {error && <ErrorState error={error} onRetry={refetch} />}
      {data && (data.runs || []).length === 0 && <EmptyState icon="🕑" title="No runs recorded yet" />}
      {data && (data.runs || []).length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>When</th><th>Status</th><th>Took</th><th>What happened</th></tr></thead>
            <tbody>
              {data.runs.map((r) => (
                <tr key={r.id}>
                  <td className="faint" title={r.started_at}>{timeAgo(r.started_at)}{r.trigger === "manual" ? " · manual" : ""}</td>
                  <td><StatusBadge status={r.status} /></td>
                  <td className="faint">{fmtDuration(r.duration_s)}</td>
                  <td><Summary text={r.summary} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Modal>
  );
}

function TasksView() {
  const t = useToast();
  const { data, loading, error, refetch } = useApi("/scripts/tasks/all");
  const builtins = useApi("/scripts/actions/builtin");
  const jobs = useApi("/scripts/jobs/builtin");
  const reg = useApi("/scripts/jobs/registry");
  const scripts = useApi("/scripts");
  const [editing, setEditing] = useState(null);
  const [running, setRunning] = useState(null);
  const [history, setHistory] = useState(null);
  const regJobs = reg.data?.jobs || [];
  const regById = Object.fromEntries(regJobs.map((j) => [j.id, j]));
  const systemJobs = regJobs.filter((j) => j.executor !== "external");

  async function runAction(action) {
    setRunning("builtin:" + action);
    try {
      await api.post("/scripts/run-now", { action });
      t.success(`Ran ${action}`);
      setTimeout(reg.refetch, 1500);
    } catch (e) {
      t.error(e.message);
    } finally {
      setRunning(null);
    }
  }

  const actions = builtins.data?.actions || [];

  async function save(form) {
    const body = {
      name: form.name, cron: form.cron, action: form.action,
      script_id: form.action === "script" ? Number(form.script_id) || null : null,
      enabled: form.enabled,
    };
    try {
      if (form.id) await api.put(`/scripts/tasks/${form.id}`, body);
      else await api.post("/scripts/tasks", body);
      t.success("Saved");
      setEditing(null);
      refetch();
    } catch (e) {
      t.error(e.message);
    }
  }

  async function toggle(task) {
    try {
      await api.post(`/scripts/tasks/${task.id}/${task.enabled ? "disable" : "enable"}`);
      refetch();
    } catch (e) {
      t.error(e.message);
    }
  }

  async function remove(task) {
    if (!confirm(`Delete task "${task.name}"?`)) return;
    try {
      await api.del(`/scripts/tasks/${task.id}`);
      t.success("Deleted");
      refetch();
    } catch (e) {
      t.error(e.message);
    }
  }

  async function runNow(task) {
    setRunning(task.id);
    const action = task.action === "script" ? `script:${task.script_id}` : task.action;
    try {
      await api.post("/scripts/run-now", { action });
      t.success("Ran now");
      refetch();
    } catch (e) {
      t.error(e.message);
    } finally {
      setRunning(null);
    }
  }

  return (
    <div>
      <div className="row mb-3" style={{ justifyContent: "flex-end" }}>
        <button className="btn btn-primary btn-sm" onClick={() => setEditing({ ...EMPTY_TASK, action: actions[0] || "script" })}>
          <IconPlus size={14} /> New task
        </button>
      </div>

      <div className="card mb-3">
        <div className="section-title">Built-in scheduled actions</div>
        <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>
          These run automatically on the interval shown (e.g. the watched-movie mover). Run any one now.
        </div>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Action</th><th>Schedule</th><th>Last run</th><th>Last result</th><th></th></tr></thead>
            <tbody>
              {(jobs.data?.jobs || []).map((j) => {
                const r = regById["managarr." + j.action];
                return (
                  <tr key={j.action}>
                    <td>
                      <span className="badge badge-muted">{j.action}</span>
                      {r?.description && <div className="faint" style={{ fontSize: 11, marginTop: 4 }}>{r.description}</div>}
                    </td>
                    <td className="faint">{j.schedule}</td>
                    <td>{r ? <><StatusBadge status={r.last_status} /> <span className="faint" style={{ fontSize: 12 }}>{r.last_run_at ? timeAgo(r.last_run_at) : ""}</span></> : <span className="faint">—</span>}</td>
                    <td><Summary text={r?.last_summary} /></td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      {r && <button className="btn btn-ghost btn-xs" onClick={() => setHistory(r)}>History</button>}{" "}
                      <button className="btn btn-success btn-xs" onClick={() => runAction(j.action)} disabled={running === "builtin:" + j.action}>
                        <IconPlay size={11} /> {running === "builtin:" + j.action ? "…" : "Run now"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {systemJobs.length > 0 && (
        <div className="card mb-3">
          <div className="section-title">System jobs for Managarr</div>
          <div className="faint" style={{ fontSize: 12, marginBottom: 8 }}>
            Jobs that run outside Managarr but keep its library working — image updates, download repair, the Audible scan.
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Job</th><th>Schedule</th><th>Last run</th><th>Last result</th><th></th></tr></thead>
              <tbody>
                {systemJobs.map((j) => (
                  <tr key={j.id}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{j.title}{!j.enabled && <span className="badge badge-muted" style={{ marginLeft: 6 }}>paused</span>}</div>
                      {j.description && <div className="faint" style={{ fontSize: 11, marginTop: 4, maxWidth: 420 }}>{j.description}</div>}
                    </td>
                    <td className="faint">{j.schedule_human || j.schedule}</td>
                    <td><StatusBadge status={j.last_status} /> <span className="faint" style={{ fontSize: 12 }}>{j.last_run_at ? timeAgo(j.last_run_at) : ""}</span></td>
                    <td><Summary text={j.last_summary} /></td>
                    <td><button className="btn btn-ghost btn-xs" onClick={() => setHistory(j)}>History</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <HistoryModal job={history} onClose={() => setHistory(null)} />

      {editing && (
        <TaskForm
          initial={editing}
          actions={actions}
          scripts={scripts.data || []}
          onCancel={() => setEditing(null)}
          onSave={save}
        />
      )}

      {loading && <Loading />}
      {error && <ErrorState error={error} onRetry={refetch} />}
      {!loading && !error && (data || []).length === 0 && (
        <EmptyState icon="⏰" title="No scheduled tasks" hint="Schedule a builtin action or script via cron." />
      )}

      {(data || []).length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr><th>Name</th><th>Cron</th><th>Action</th><th>Last run</th><th>Enabled</th><th></th></tr>
            </thead>
            <tbody>
              {data.map((task) => (
                <tr key={task.id}>
                  <td style={{ fontWeight: 600 }}>{task.name}</td>
                  <td style={{ fontFamily: "monospace", fontSize: 12 }}>{task.cron}</td>
                  <td>
                    <span className="badge badge-muted">{task.action === "script" ? `script #${task.script_id}` : task.action}</span>
                    {task.last_status && <span className={"badge " + (task.last_status === "error" ? "badge-error" : "badge-ok")} style={{ marginLeft: 6 }}>{task.last_status}</span>}
                  </td>
                  <td className="faint">{task.last_run ? timeAgo(task.last_run) : "never"}</td>
                  <td><Switch checked={task.enabled} onChange={() => toggle(task)} /></td>
                  <td className="row gap-sm">
                    <button className="btn btn-success btn-xs" onClick={() => runNow(task)} disabled={running === task.id}>
                      <IconPlay size={11} /> {running === task.id ? "…" : "Run"}
                    </button>
                    {regById["managarr.task-" + task.id] && (
                      <button className="btn btn-ghost btn-xs" onClick={() => setHistory(regById["managarr.task-" + task.id])}>History</button>
                    )}
                    <button className="btn btn-ghost btn-xs" onClick={() => setEditing(task)}>Edit</button>
                    <button className="btn btn-danger btn-xs" onClick={() => remove(task)}><IconTrash size={11} /></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function TaskForm({ initial, actions, scripts, onCancel, onSave }) {
  const [form, setForm] = useState({ ...initial });
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  function submit(e) { e.preventDefault(); onSave(form); }
  return (
    <form className="card mb-3" onSubmit={submit}>
      <div className="card-title">{form.id ? "Edit task" : "New task"}</div>
      <div className="grid-2">
        <div className="field">
          <label className="input-label">Name</label>
          <input className="input" value={form.name} onChange={(e) => set("name", e.target.value)} required />
        </div>
        <div className="field">
          <label className="input-label">Cron (5-field)</label>
          <input className="input" style={{ fontFamily: "monospace" }} value={form.cron} onChange={(e) => set("cron", e.target.value)} placeholder="0 3 * * *" required />
        </div>
      </div>
      <div className="grid-2">
        <div className="field">
          <label className="input-label">Action</label>
          <select className="select" value={form.action} onChange={(e) => set("action", e.target.value)}>
            {actions.map((a) => <option key={a} value={a}>{a}</option>)}
            <option value="script">script</option>
          </select>
        </div>
        {form.action === "script" && (
          <div className="field">
            <label className="input-label">Script</label>
            <select className="select" value={form.script_id || ""} onChange={(e) => set("script_id", e.target.value)}>
              <option value="">— pick —</option>
              {scripts.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>
        )}
      </div>
      <div className="row">
        <Switch checked={form.enabled} onChange={(v) => set("enabled", v)} />
        <span className="muted" style={{ fontSize: 13 }}>Enabled</span>
        <div style={{ flex: 1 }} />
        <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel}>Cancel</button>
        <button type="submit" className="btn btn-primary btn-sm">Save</button>
      </div>
    </form>
  );
}
