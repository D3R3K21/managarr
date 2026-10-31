import React, { createContext, useContext, useState, useCallback } from "react";

const ToastContext = createContext(null);

let _id = 0;

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const dismiss = useCallback((id) => {
    setToasts((t) => t.filter((x) => x.id !== id));
  }, []);

  const toast = useCallback(
    (message, kind = "info", duration = 3500) => {
      const id = ++_id;
      setToasts((t) => [...t, { id, message, kind }]);
      if (duration > 0) setTimeout(() => dismiss(id), duration);
      return id;
    },
    [dismiss]
  );

  const helpers = {
    toast,
    success: (m, d) => toast(m, "success", d),
    error: (m, d) => toast(m, "error", d),
    info: (m, d) => toast(m, "info", d),
    dismiss,
    toasts,
  };

  return (
    <ToastContext.Provider value={helpers}>
      {children}
      <ToastViewport toasts={toasts} dismiss={dismiss} />
    </ToastContext.Provider>
  );
}

function ToastViewport({ toasts, dismiss }) {
  return (
    <div className="toast-wrap">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={"toast " + (t.kind || "info")}
          onClick={() => dismiss(t.id)}
          role="status"
        >
          {t.message}
        </div>
      ))}
    </div>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}
