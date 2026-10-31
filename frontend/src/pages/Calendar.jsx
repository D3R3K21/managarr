import React, { useState, useMemo } from "react";
import api from "../api/client";
import { useApi } from "../hooks/useApi";
import { useToast } from "../hooks/useToast";
import PageHeader from "../components/PageHeader";
import Modal from "../components/Modal";
import Switch from "../components/Switch";
import { Loading, ErrorState } from "../components/States";
import { formatDate } from "../utils/format";
import { IconRefresh } from "../components/icons";

// Local-date ISO (toISOString would shift across the UTC boundary).
const iso = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

// The visible grid for a month: full weeks, Sunday-first, so leading/trailing
// days of the neighbouring months are included.
function monthGrid(offset = 0) {
  const now = new Date();
  const first = new Date(now.getFullYear(), now.getMonth() + offset, 1);
  const last = new Date(now.getFullYear(), now.getMonth() + offset + 1, 0);
  const gridStart = new Date(first);
  gridStart.setDate(first.getDate() - first.getDay());
  const gridEnd = new Date(last);
  gridEnd.setDate(last.getDate() + (6 - last.getDay()));
  const days = [];
  for (let d = new Date(gridStart); d <= gridEnd; d.setDate(d.getDate() + 1)) {
    days.push({ date: iso(d), inMonth: d.getMonth() === first.getMonth() });
  }
  return {
    days,
    start: iso(gridStart),
    end: iso(gridEnd),
    label: first.toLocaleString(undefined, { month: "long", year: "numeric" }),
  };
}

const KIND_ICON = { tv: "📺", movie: "🎬", audiobook: "📚" };
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MAX_CHIPS = 3;

export default function Calendar() {
  const t = useToast();
  const [offset, setOffset] = useState(0);
  const grid = useMemo(() => monthGrid(offset), [offset]);
  const { data, loading, error, refetch, setData } = useApi("/calendar", { start: grid.start, end: grid.end });
  const [syncing, setSyncing] = useState(false);
  const [selDay, setSelDay] = useState(null); // ISO date opened in the day modal

  const today = iso(new Date());
  const byDate = useMemo(() => {
    const map = {};
    for (const ev of data || []) (map[ev.date] ||= []).push(ev);
    return map;
  }, [data]);

  async function sync() {
    setSyncing(true);
    try {
      const r = await api.post("/calendar/sync");
      t.success(`Synced: ${r.tv || 0} TV, ${r.movies || 0} movies, ${r.audiobooks || 0} books`);
      refetch();
    } catch (e) {
      t.error(e.message);
    } finally {
      setSyncing(false);
    }
  }

  async function toggleAutoGrab(ev) {
    try {
      await api.patch(`/calendar/${ev.id}`, { auto_grab: !ev.auto_grab });
      setData((rows) => (rows || []).map((r) => (r.id === ev.id ? { ...r, auto_grab: !ev.auto_grab } : r)));
    } catch (e) {
      t.error(e.message);
    }
  }

  const selEvents = selDay ? byDate[selDay] || [] : [];

  return (
    <div>
      <PageHeader
        title="Calendar"
        subtitle={grid.label}
        actions={
          <>
            <button className="btn btn-ghost btn-sm" onClick={() => setOffset((o) => o - 1)}>‹ Prev</button>
            <button className="btn btn-ghost btn-sm" onClick={() => setOffset(0)}>Today</button>
            <button className="btn btn-ghost btn-sm" onClick={() => setOffset((o) => o + 1)}>Next ›</button>
            <button className="btn btn-secondary btn-sm" onClick={sync} disabled={syncing}>
              <IconRefresh size={14} /> {syncing ? "Syncing…" : "Sync"}
            </button>
          </>
        }
      />

      {loading && <Loading />}
      {error && <ErrorState error={error} onRetry={refetch} />}

      {!loading && !error && (
        <div className="cal-grid card">
          {WEEKDAYS.map((d) => (
            <div className="cal-weekday" key={d}>{d}</div>
          ))}
          {grid.days.map(({ date, inMonth }) => {
            const events = byDate[date] || [];
            const extra = events.length - MAX_CHIPS;
            return (
              <div
                key={date}
                className={
                  "cal-day" +
                  (inMonth ? "" : " cal-day-out") +
                  (date === today ? " cal-day-today" : "") +
                  (events.length ? " cal-day-has-events" : "")
                }
                role={events.length ? "button" : undefined}
                tabIndex={events.length ? 0 : undefined}
                onClick={() => events.length && setSelDay(date)}
                onKeyDown={(e) => e.key === "Enter" && events.length && setSelDay(date)}
              >
                <div className="cal-day-num">{Number(date.slice(8, 10))}</div>
                {events.slice(0, MAX_CHIPS).map((ev) => (
                  <div
                    className={"cal-chip cal-chip-" + ev.media_kind + (ev.grabbed ? " cal-chip-grabbed" : "")}
                    key={ev.id}
                    title={`${ev.title} (${ev.event_type})${ev.grabbed ? " — grabbed" : ev.auto_grab ? " — auto-grab on" : ""}`}
                  >
                    <span>{KIND_ICON[ev.media_kind] || "•"}</span>
                    <span className="cal-chip-title">{ev.title}</span>
                    {ev.grabbed && <span className="cal-chip-mark">✓</span>}
                  </div>
                ))}
                {extra > 0 && <div className="cal-chip cal-chip-more">+{extra} more</div>}
              </div>
            );
          })}
        </div>
      )}

      <Modal open={!!selDay} title={selDay ? formatDate(selDay) : ""} onClose={() => setSelDay(null)}>
        <div className="list">
          {selEvents.map((ev) => (
            <div className="list-item" key={ev.id}>
              <span style={{ fontSize: 18 }}>{KIND_ICON[ev.media_kind] || "•"}</span>
              <div className="list-item-main">
                <div className="list-item-title">{ev.title}</div>
                <div className="list-item-sub">
                  <span className="badge badge-muted">{ev.event_type}</span>
                  {ev.grabbed && <span className="badge badge-ok" style={{ marginLeft: 6 }}>grabbed</span>}
                </div>
              </div>
              <span className="faint" style={{ fontSize: 12 }}>auto-grab</span>
              <Switch checked={ev.auto_grab} onChange={() => toggleAutoGrab(ev)} title="Auto-grab on release" />
            </div>
          ))}
        </div>
      </Modal>
    </div>
  );
}
