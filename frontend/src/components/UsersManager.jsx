import React, { useState } from "react";
import api from "../api/client";
import { useApi } from "../hooks/useApi";
import { useToast } from "../hooks/useToast";
import { Loading, ErrorState } from "./States";
import { IconPlus, IconTrash } from "./icons";

// Admin-only account management. The env bootstrap admin shows as a read-only row.
export default function UsersManager() {
  const t = useToast();
  const { data, loading, error, refetch } = useApi("/users");
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [role, setRole] = useState("user");
  const [busy, setBusy] = useState(false);

  async function add(e) {
    e.preventDefault();
    if (!u.trim() || !p) return;
    setBusy(true);
    try {
      await api.post("/users", { username: u.trim(), password: p, role });
      t.success(`Added ${u.trim()}`);
      setU(""); setP(""); setRole("user");
      refetch();
    } catch (e) { t.error(e.message); } finally { setBusy(false); }
  }
  async function remove(usr) {
    if (!window.confirm(`Delete user “${usr.username}”?`)) return;
    try { await api.del(`/users/${usr.id}`); t.success("Deleted"); refetch(); }
    catch (e) { t.error(e.message); }
  }
  async function resetPw(usr) {
    const np = window.prompt(`New password for “${usr.username}”:`);
    if (!np) return;
    try { await api.patch(`/users/${usr.id}`, { password: np }); t.success("Password reset"); }
    catch (e) { t.error(e.message); }
  }
  async function changeRole(usr, newRole) {
    try { await api.patch(`/users/${usr.id}`, { role: newRole }); t.success("Role updated"); refetch(); }
    catch (e) { t.error(e.message); }
  }

  const users = data || [];
  return (
    <div className="card mb-3">
      <div className="card-title">Users</div>
      <div className="faint" style={{ fontSize: 13, marginBottom: 12 }}>
        Admins manage users and settings; standard users can browse, search and grab, but can’t change
        configuration or other accounts.
      </div>

      {loading && <Loading />}
      {error && <ErrorState error={error} onRetry={refetch} />}

      <div className="list">
        {users.map((usr) => (
          <div className="list-item" key={usr.id ?? "bootstrap"}>
            <div className="list-item-main">
              <div className="list-item-title">
                {usr.username}{" "}
                {usr.bootstrap && <span className="badge badge-muted">bootstrap admin</span>}
                {usr.is_active === false && <span className="badge badge-error">disabled</span>}
              </div>
              <div className="list-item-sub">{usr.role}</div>
            </div>
            {!usr.bootstrap && (
              <div className="row gap-sm" style={{ alignItems: "center" }}>
                <select className="input" value={usr.role} onChange={(e) => changeRole(usr, e.target.value)} style={{ maxWidth: 110 }}>
                  <option value="user">user</option>
                  <option value="admin">admin</option>
                </select>
                <button className="btn btn-ghost btn-xs" onClick={() => resetPw(usr)}>Reset password</button>
                <button className="btn btn-danger btn-xs" onClick={() => remove(usr)} title="Delete user"><IconTrash size={12} /></button>
              </div>
            )}
          </div>
        ))}
      </div>

      <form className="row gap-sm" onSubmit={add} style={{ marginTop: 12, flexWrap: "wrap" }}>
        <input className="input" placeholder="username" value={u} onChange={(e) => setU(e.target.value)} style={{ maxWidth: 180 }} />
        <input className="input" type="password" placeholder="password" value={p} onChange={(e) => setP(e.target.value)} style={{ maxWidth: 180 }} />
        <select className="input" value={role} onChange={(e) => setRole(e.target.value)} style={{ maxWidth: 110 }}>
          <option value="user">user</option>
          <option value="admin">admin</option>
        </select>
        <button className="btn btn-primary btn-sm" disabled={busy}><IconPlus size={14} /> Add user</button>
      </form>
    </div>
  );
}
