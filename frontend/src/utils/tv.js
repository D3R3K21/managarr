// TV-mode detection + setup.
//
// The Managarr TV app (Shield WebView) announces itself in its User-Agent
// ("ManagarrTV/<ver>"), so the web app can switch to a TV-optimised view on every
// page — bigger, D-pad friendly, no auto-keyboard — without affecting phone/desktop.
// `?tv=1` forces it on (persisted) and `?tv=0` clears it, for testing in a browser.
export const isTvApp = (() => {
  try {
    const q = window.location.search || "";
    if (/[?&]tv=1\b/.test(q)) localStorage.setItem("managarr_tv", "1");
    if (/[?&]tv=0\b/.test(q)) localStorage.removeItem("managarr_tv");
    return /ManagarrTV/i.test(navigator.userAgent) || localStorage.getItem("managarr_tv") === "1";
  } catch {
    return false;
  }
})();

// Call once at startup (before render). Marks the root for CSS and pins a comfortable
// logical viewport width so a 4K TV panel doesn't render the UI massively zoomed-in.
export function setupTvMode() {
  if (!isTvApp) return;
  document.documentElement.classList.add("tv-mode");
  const vp = document.querySelector('meta[name="viewport"]');
  if (vp) vp.setAttribute("content", "width=1600, user-scalable=no");
  // Take manual control of scroll on history nav so Back restores our saved position
  // instead of the browser resetting it to 0 (see useTvNavFocus).
  if ("scrollRestoration" in history) history.scrollRestoration = "manual";
}
