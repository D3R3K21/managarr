import { useEffect } from "react";
import { useLocation, useNavigationType } from "react-router-dom";

// Per-history-entry scroll memory so Back returns you where you were (in-memory is
// fine — the TV app is one continuous document load).
const scrollByKey = new Map();

// Pages whose content is an async-loaded card grid — wait for a card before acting.
const GRID_PATHS = ["/movies", "/tv", "/audiobooks", "/collections", "/discover", "/watchlist"];

// Focus target: the first library card currently visible (works at the top or at a
// restored scroll position), else the first visible focusable control.
function pickTarget(body) {
  if (!body) return null;
  const focusables = [...body.querySelectorAll(
    'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])'
  )].filter((el) => el.offsetParent !== null);
  // Cards carry a monitored toggle (checkbox); target the card LINK, not the toggle.
  const cards = focusables.filter((el) => el.tagName === "A" && el.closest(".media-grid"));
  const pool = cards.length ? cards : focusables;
  const vis = pool.find((el) => {
    const r = el.getBoundingClientRect();
    return r.top >= 56 && r.top < window.innerHeight;
  });
  return vis || pool[0] || null;
}

// TV nav ergonomics: forward nav lands focus on the first card; Back restores the
// prior scroll position and focuses a visible card — never the sidebar. This hook is
// the sole owner of scroll in TV mode (useScrollRestore is disabled there).
export default function useTvNavFocus(enabled) {
  const location = useLocation();
  const navType = useNavigationType();

  // Remember the current entry's scroll as it moves.
  useEffect(() => {
    if (!enabled) return undefined;
    const onScroll = () => scrollByKey.set(location.key, window.scrollY);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [enabled, location.key]);

  useEffect(() => {
    if (!enabled) return undefined;
    const key = location.key;
    const isGrid = GRID_PATHS.includes(location.pathname);
    const targetY = navType === "POP" && scrollByKey.has(key) ? scrollByKey.get(key) : 0;
    let obs = null, fallback = null, done = false, rafId = 0;

    const cleanup = () => {
      if (obs) { obs.disconnect(); obs = null; }
      if (fallback) { clearTimeout(fallback); fallback = null; }
      if (rafId) { cancelAnimationFrame(rafId); rafId = 0; }
    };
    const contentReady = () => {
      const body = document.querySelector(".page-body");
      if (!body) return false;
      if (isGrid) return !!body.querySelector(".media-grid a");
      return !!(body.querySelector(".media-grid a") || body.querySelector(".list-item"));
    };
    const go = () => {
      if (done) return;
      done = true;
      if (obs) { obs.disconnect(); obs = null; }
      if (fallback) { clearTimeout(fallback); fallback = null; }
      // Re-assert the scroll over a few frames — a big grid finishes laying out after
      // the first card appears, and a single scrollTo can clamp before it's tall.
      let n = 0;
      const step = () => {
        window.scrollTo(0, targetY);
        if (++n < 10) { rafId = requestAnimationFrame(step); }
        else {
          rafId = 0;
          const el = pickTarget(document.querySelector(".page-body"));
          if (el) { try { el.focus({ preventScroll: true }); } catch { /* ignore */ } }
        }
      };
      step();
    };

    const start = setTimeout(() => {
      if (contentReady()) { go(); return; }
      const body = document.querySelector(".page-body") || document.body;
      obs = new MutationObserver(() => { if (contentReady()) go(); });
      obs.observe(body, { childList: true, subtree: true });
      fallback = setTimeout(go, isGrid ? 6000 : 1000);
    }, 50);

    return () => { clearTimeout(start); cleanup(); };
  }, [enabled, location.key]); // eslint-disable-line react-hooks/exhaustive-deps
}
