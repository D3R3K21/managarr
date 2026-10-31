import React, { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { QRCodeSVG } from "qrcode.react";
import api from "../api/client";
import { useAuth } from "../context/AuthContext";
import { isTvApp } from "../utils/tv";
import { IconLogo, IconShield } from "../components/icons";

export default function Login() {
  const [mode, setMode] = useState("password");   // "password" | "pair"
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state && location.state.from && location.state.from.pathname) || "/";

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(username, password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err.message || "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-wrap">
      <div className="login-card">
        <div className="login-brand">
          <IconLogo size={26} />
          <span>Managarr</span>
        </div>

        {mode === "password" ? (
          <>
            <form onSubmit={handleSubmit} className="col">
              <div className="field">
                <label className="input-label">Username</label>
                <input
                  className="input"
                  type="text"
                  value={username}
                  autoFocus={!isTvApp}
                  required
                  onChange={(e) => setUsername(e.target.value)}
                />
              </div>
              <div className="field">
                <label className="input-label">Password</label>
                <input
                  className="input"
                  type="password"
                  value={password}
                  required
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
              {error && (
                <div style={{ color: "var(--err)", fontSize: 13, textAlign: "center" }}>{error}</div>
              )}
              <button
                className="btn btn-primary"
                type="submit"
                disabled={loading}
                style={{ width: "100%", justifyContent: "center", marginTop: 6 }}
              >
                {loading ? "Signing in…" : "Sign in"}
              </button>
            </form>
            <div style={{ textAlign: "center", marginTop: 16 }}>
              <button className="btn btn-ghost btn-sm" onClick={() => setMode("pair")}>
                <IconShield size={14} /> Pair this device instead
              </button>
            </div>
          </>
        ) : (
          <PairDevice from={from} onUsePassword={() => setMode("password")} />
        )}
      </div>
    </div>
  );
}

// Device-authorization flow: request a code, show it + a QR, poll until a
// logged-in user approves it elsewhere, then sign in with the issued token.
function PairDevice({ from, onUsePassword }) {
  const { applyToken } = useAuth();
  const navigate = useNavigate();
  const [pairing, setPairing] = useState(null);   // {device_code, user_code, interval}
  const [status, setStatus] = useState("loading"); // loading | waiting | expired | error
  const [errMsg, setErrMsg] = useState("");
  const timer = useRef(null);

  const start = useCallback(async () => {
    setStatus("loading");
    setErrMsg("");
    setPairing(null);
    try {
      const label = /ManagarrTV/i.test(navigator.userAgent) ? "Managarr TV" : "Web device";
      const p = await api.post("/auth/device/start", { label });
      setPairing(p);
      setStatus("waiting");
    } catch (e) {
      setErrMsg(e.message || "Couldn't start pairing");
      setStatus("error");
    }
  }, []);

  useEffect(() => { start(); }, [start]);

  // Poll for approval while waiting.
  useEffect(() => {
    if (status !== "waiting" || !pairing) return undefined;
    const interval = (pairing.interval || 3) * 1000;
    timer.current = setInterval(async () => {
      try {
        const r = await api.get("/auth/device/poll", { device_code: pairing.device_code });
        if (r.status === "approved") {
          clearInterval(timer.current);
          applyToken(r.token, r.username, r.role);
          navigate(from, { replace: true });
        } else if (r.status === "expired") {
          clearInterval(timer.current);
          setStatus("expired");
        }
      } catch { /* transient — keep polling */ }
    }, interval);
    return () => clearInterval(timer.current);
  }, [status, pairing, applyToken, navigate, from]);

  const verifyUrl = pairing
    ? `${window.location.origin}/link?code=${encodeURIComponent(pairing.user_code)}`
    : "";

  return (
    <div className="col" style={{ gap: 14, alignItems: "center", textAlign: "center" }}>
      {status === "loading" && <div className="faint" style={{ padding: 20 }}>Getting a code…</div>}

      {status === "error" && (
        <>
          <div style={{ color: "var(--err)", fontSize: 13 }}>{errMsg}</div>
          <button className="btn btn-secondary btn-sm" onClick={start}>Try again</button>
        </>
      )}

      {status === "expired" && (
        <>
          <div className="faint" style={{ fontSize: 13 }}>This code expired.</div>
          <button className="btn btn-primary btn-sm" onClick={start}>Get a new code</button>
        </>
      )}

      {status === "waiting" && pairing && (
        <>
          <div style={{ background: "#fff", padding: 12, borderRadius: 10 }}>
            <QRCodeSVG value={verifyUrl} size={188} bgColor="#ffffff" fgColor="#0e0e13"
                       level="M" marginSize={1} />
          </div>
          <div className="faint" style={{ fontSize: 13, lineHeight: 1.5 }}>
            Scan with your phone, or on any signed-in device go to<br />
            <strong style={{ color: "var(--text)" }}>Settings → Authorize a device</strong> and enter:
          </div>
          <div style={{
            fontFamily: "monospace", fontSize: 30, fontWeight: 800, letterSpacing: 4,
            color: "var(--accent-soft)", background: "var(--bg-3)", padding: "10px 18px",
            borderRadius: 8, border: "1px solid var(--border-2)",
          }}>
            {pairing.user_code}
          </div>
          <div className="row" style={{ gap: 6, color: "var(--text-dim)", fontSize: 12 }}>
            <span className="spin"><IconShield size={13} /></span> Waiting for approval…
          </div>
        </>
      )}

      <button className="btn btn-ghost btn-sm" onClick={onUsePassword} style={{ marginTop: 4 }}>
        Use a password instead
      </button>
    </div>
  );
}
