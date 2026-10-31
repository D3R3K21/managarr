import { useState, useEffect, useRef } from "react";
import { isTvApp } from "../utils/tv";

// Like useState but persists to sessionStorage under `key`, so search/filter state
// survives navigating to a detail page and back (per-tab; cleared when the tab closes).
export function usePersistedState(key, initial) {
  const [val, setVal] = useState(() => {
    try {
      const raw = sessionStorage.getItem(key);
      return raw !== null ? JSON.parse(raw) : initial;
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      sessionStorage.setItem(key, JSON.stringify(val));
    } catch {
      /* ignore quota / serialization errors */
    }
  }, [key, val]);
  return [val, setVal];
}

// Save the window scroll position for `key` on unmount and restore it once the page
// is `ready` (i.e. its list data has loaded), so Back returns you to the same spot.
export function useScrollRestore(key, ready = true) {
  const restored = useRef(false);
  const skey = "scroll:" + key;

  useEffect(() => {
    if (isTvApp) return; // TV mode: useTvNavFocus owns scroll + focus
    if (ready && !restored.current) {
      const saved = sessionStorage.getItem(skey);
      if (saved) requestAnimationFrame(() => window.scrollTo(0, parseInt(saved, 10) || 0));
      restored.current = true;
    }
  }, [skey, ready]);

  useEffect(() => {
    if (isTvApp) return undefined;
    return () => sessionStorage.setItem(skey, String(window.scrollY));
  }, [skey]);
}
