export function formatBytes(bytes) {
  if (bytes == null || isNaN(bytes)) return "—";
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const v = bytes / Math.pow(1024, i);
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

export function formatDuration(seconds) {
  if (!seconds) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  if (h) return `${h}h ${m}m`;
  return `${m}m`;
}

export function formatEta(seconds) {
  if (seconds == null || seconds < 0 || seconds >= 8640000) return "∞";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s}s`;
  return `${s}s`;
}

export function formatDate(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  } catch {
    return iso;
  }
}

export function formatDateTime(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso.endsWith && !iso.endsWith("Z") && iso.includes("T") ? iso + "Z" : iso);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function timeAgo(iso) {
  if (!iso) return "—";
  let d;
  try {
    d = new Date(iso.includes && iso.includes("T") && !iso.endsWith("Z") ? iso + "Z" : iso);
  } catch {
    return iso;
  }
  if (isNaN(d.getTime())) return iso;
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return formatDate(iso);
}

export function pct(v) {
  if (v == null) return 0;
  return Math.max(0, Math.min(100, Math.round(v * 100)));
}

// Pull a provider id/url out of an item's meta blob.
export function providers(item) {
  return ((item && item.meta && item.meta.providers) || {});
}

// trailers live at meta.trailers = [{site, key}]
export function trailers(item) {
  const t = (item && item.meta && item.meta.trailers) || [];
  return Array.isArray(t) ? t : [];
}
