import React, { useEffect, useRef } from "react";
import { IconX } from "./icons";
import { isTvApp } from "../utils/tv";

// Lightweight modal. Click backdrop or the X (or press Escape) to close.
// props: open, onClose, title?, footer?, width?, focusDep?, children
// focusDep: change it (e.g. per wizard step) to re-run the TV auto-focus.
export default function Modal({ open, onClose, title, footer, width = 560, focusDep, children }) {
  const modalRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    function onKey(e) {
      if (e.key === "Escape") onClose && onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // On a TV, move focus to the obvious action so the flow is drive-by-OK. Priority:
  // an explicit [data-autofocus], then the primary button, then the first body control,
  // then the first footer button (never a destructive btn-danger by default).
  useEffect(() => {
    if (!open || !isTvApp) return undefined;
    const t = setTimeout(() => {
      const root = modalRef.current;
      if (!root) return;
      const pick =
        root.querySelector("[data-autofocus]:not([disabled])") ||
        root.querySelector(".modal-foot .btn-primary:not([disabled])") ||
        root.querySelector(
          '.modal-body button:not([disabled]), .modal-body a[href], .modal-body input:not([disabled]), .modal-body select, .modal-body textarea, .modal-body [tabindex]:not([tabindex="-1"])'
        ) ||
        root.querySelector(".modal-foot button:not([disabled])");
      if (pick) { try { pick.focus(); } catch { /* ignore */ } }
    }, 40);
    return () => clearTimeout(t);
  }, [open, focusDep]);

  if (!open) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal"
        ref={modalRef}
        style={{ maxWidth: width }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="modal-head">
          <div className="modal-title">{title}</div>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            <IconX size={16} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}
