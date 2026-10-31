import React, { useEffect, useState, useCallback } from "react";
import api from "../api/client";
import { useToast } from "../hooks/useToast";
import { IconCheck, IconShield } from "./icons";

// Approve a device-pairing code (the logged-in side of the device-auth flow).
// Used on the /link page (QR target, code prefilled) and in Settings (type the code).
// props: initialCode?, onApproved?
export default function AuthorizeDevice({ initialCode = "", onApproved }) {
  const t = useToast();
  const [code, setCode] = useState(initialCode);
  const [info, setInfo] = useState(null);      // {device_label,...} once looked up
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  const normalized = code.toUpperCase().replace(/[^A-Z0-9]/g, "");
  const ready = normalized.length >= 8;

  // Look up the code (once it's long enough) to show what's being authorized.
  useEffect(() => {
    if (!ready || done) { setInfo(null); return; }
    let cancelled = false;
    api.get("/auth/device/lookup", { user_code: normalized })
      .then((d) => { if (!cancelled) setInfo(d); })
      .catch(() => { if (!cancelled) setInfo(null); });
    return () => { cancelled = true; };
  }, [normalized, ready, done]);

  const approve = useCallback(async () => {
    setBusy(true);
    try {
      const r = await api.post("/auth/device/approve", { user_code: normalized });
      setDone(true);
      t.success(`Authorized ${r.device_label || "device"}`);
      onApproved && onApproved(r);
    } catch (e) {
      t.error(e.message);
    } finally {
      setBusy(false);
    }
  }, [normalized, onApproved, t]);

  if (done) {
    return (
      <div className="row" style={{ gap: 10, color: "var(--ok)" }}>
        <IconCheck size={18} />
        <div>
          <div style={{ fontWeight: 600 }}>Device authorized</div>
          <div className="faint" style={{ fontSize: 12 }}>
            {info?.device_label ? `${info.device_label} will ` : "It'll "}sign in on its own now.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="col" style={{ gap: 10 }}>
      <div className="row" style={{ gap: 8, alignItems: "flex-end", flexWrap: "wrap" }}>
        <label style={{ flex: 1, minWidth: 180 }}>
          <div className="input-label">Device code</div>
          <input
            className="input"
            value={code}
            autoFocus={!initialCode}
            placeholder="XXXX-XXXX"
            spellCheck={false}
            autoCapitalize="characters"
            style={{ fontFamily: "monospace", fontSize: 18, letterSpacing: 2, textTransform: "uppercase" }}
            onChange={(e) => setCode(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && ready && !busy) approve(); }}
          />
        </label>
        <button className="btn btn-primary" disabled={!ready || busy} onClick={approve}
                style={{ height: 38 }}>
          <IconShield size={15} /> {busy ? "Authorizing…" : "Authorize"}
        </button>
      </div>
      {info && (
        <div className="faint" style={{ fontSize: 12 }}>
          Authorizing <strong>{info.device_label || "a device"}</strong> to sign in as you.
        </div>
      )}
    </div>
  );
}
