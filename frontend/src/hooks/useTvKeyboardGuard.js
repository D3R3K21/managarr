import { useEffect } from "react";

// On a TV, an <input> pops the on-screen keyboard the instant D-pad focus lands on
// it — jarring when you're just arrowing past. This guard keeps text fields readOnly
// (focusable, shows the ring, but no keyboard) until you actually select them
// (click / D-pad center / Enter); leaving the field restores readOnly.
const TEXTY_TYPES = new Set(["text", "password", "search", "email", "url", "tel", "number", ""]);

function isTexty(el) {
  if (!el || el.nodeType !== 1) return false;
  if (el.tagName === "TEXTAREA") return true;
  if (el.tagName !== "INPUT") return false;
  return TEXTY_TYPES.has((el.getAttribute("type") || "").toLowerCase());
}

export default function useTvKeyboardGuard(enabled) {
  useEffect(() => {
    if (!enabled) return undefined;

    const guard = (el) => {
      if (isTexty(el) && !el.dataset.tvGuarded) {
        el.dataset.tvGuarded = "1";
        el.readOnly = true;
      }
    };
    const scan = (root) =>
      (root.querySelectorAll ? root.querySelectorAll("input, textarea") : []).forEach(guard);

    scan(document);
    const mo = new MutationObserver((muts) => {
      for (const m of muts) {
        for (const n of m.addedNodes) {
          if (n.nodeType !== 1) continue;
          guard(n);
          scan(n);
        }
      }
    });
    mo.observe(document.body, { childList: true, subtree: true });

    // Selecting the field (click / D-pad center / Enter) makes it editable and opens the
    // IME in one press. Android only shows the keyboard for a *gesture-driven* focus, so we
    // re-focus synchronously inside the event; the tvActivating flag stops the blur() below
    // from re-arming the guard mid-activation.
    const activate = (el) => {
      if (isTexty(el) && el.dataset.tvGuarded && el.readOnly) {
        el.dataset.tvActivating = "1";
        el.readOnly = false;
        try { el.blur(); el.focus(); } catch { /* ignore */ }
        delete el.dataset.tvActivating;
      }
    };
    const onClick = (e) => activate(e.target);
    const onKeyDown = (e) => {
      if ((e.key === "Enter" || e.key === " ") && isTexty(e.target) && e.target.readOnly) {
        e.preventDefault();
        activate(e.target);
      }
    };
    // Leaving the field re-arms the guard so the next focus won't pop the keyboard.
    const onFocusOut = (e) => {
      const el = e.target;
      if (isTexty(el) && el.dataset.tvGuarded && !el.dataset.tvActivating) el.readOnly = true;
    };

    document.addEventListener("click", onClick, true);
    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("focusout", onFocusOut, true);
    return () => {
      mo.disconnect();
      document.removeEventListener("click", onClick, true);
      document.removeEventListener("keydown", onKeyDown, true);
      document.removeEventListener("focusout", onFocusOut, true);
    };
  }, [enabled]);
}
