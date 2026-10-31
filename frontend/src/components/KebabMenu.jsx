import React, { useState, useRef, useEffect } from "react";

// Reusable 3-dots menu. props: items = [{ label, onClick, danger? }]
export default function KebabMenu({ items = [] }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    function onDoc(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    }
    if (open) document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  if (!items.length) return null;

  return (
    <div className="kebab" ref={ref}>
      <button
        className="btn btn-ghost btn-sm kebab-btn"
        onClick={() => setOpen((o) => !o)}
        aria-label="More actions"
        title="More"
      >
        ⋮
      </button>
      {open && (
        <div className="kebab-menu" role="menu">
          {items.map((it, i) => (
            <button
              key={i}
              className={"kebab-item" + (it.danger ? " danger" : "")}
              onClick={() => {
                setOpen(false);
                it.onClick && it.onClick();
              }}
            >
              {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
