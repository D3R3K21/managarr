import React from "react";
import { IconRefresh } from "./icons";

export function Loading({ label = "Loading..." }) {
  return (
    <div className="state">
      <div className="state-icon">
        <span className="spin"><IconRefresh size={30} /></span>
      </div>
      <div className="muted">{label}</div>
    </div>
  );
}

export function ErrorState({ error, onRetry }) {
  const msg = (error && (error.message || String(error))) || "Something went wrong";
  return (
    <div className="state error">
      <div className="state-icon">⚠</div>
      <div className="state-title">Failed to load</div>
      <div className="muted">{msg}</div>
      {onRetry && (
        <button className="btn btn-secondary btn-sm mt-3" onClick={onRetry}>
          <IconRefresh size={14} /> Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({ icon = "📭", title = "Nothing here yet", hint }) {
  return (
    <div className="state">
      <div className="state-icon">{icon}</div>
      <div className="state-title">{title}</div>
      {hint && <div className="muted">{hint}</div>}
    </div>
  );
}
