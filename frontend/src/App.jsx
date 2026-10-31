import React, { useState, useEffect } from "react";
import { Routes, Route, NavLink, Navigate, useLocation } from "react-router-dom";
import { AuthProvider, RequireAuth, useAuth } from "./context/AuthContext";
import { ToastProvider } from "./hooks/useToast";
import GlobalSearch from "./components/GlobalSearch";
import {
  IconFilm, IconTv, IconBook, IconLibrary, IconCompass, IconActivity,
  IconSearch, IconSliders, IconBookmark, IconCalendar, IconConvert, IconCode,
  IconLogo, IconChat, IconSettings, IconHealth, IconLogout, IconDash,
} from "./components/icons";

import useTvKeyboardGuard from "./hooks/useTvKeyboardGuard";
import useTvNavFocus from "./hooks/useTvNavFocus";
import { isTvApp } from "./utils/tv";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Movies from "./pages/Movies";
import TV from "./pages/TV";
import Audiobooks from "./pages/Audiobooks";
import MovieDetail from "./pages/MovieDetail";
import SeriesDetail from "./pages/SeriesDetail";
import AudiobookDetail from "./pages/AudiobookDetail";
import Collections from "./pages/Collections";
import CollectionDetail from "./pages/CollectionDetail";
import Discover from "./pages/Discover";
import Calendar from "./pages/Calendar";
import Activity from "./pages/Activity";
import Indexers from "./pages/Indexers";
import Quality from "./pages/Quality";
import Watchlist from "./pages/Watchlist";
import Transcode from "./pages/Transcode";
import Scripts from "./pages/Scripts";
import Health from "./pages/Health";
import Settings from "./pages/Settings";
import Chat from "./pages/Chat";
import LinkDevice from "./pages/LinkDevice";

const NAV_GROUPS = [
  {
    label: "Library",
    items: [
      { to: "/", label: "Dashboard", icon: IconDash, end: true },
      { to: "/movies", label: "Movies", icon: IconFilm },
      { to: "/tv", label: "TV", icon: IconTv },
      { to: "/audiobooks", label: "Audiobooks", icon: IconBook },
      { to: "/collections", label: "Collections", icon: IconLibrary },
      { to: "/discover", label: "Discover", icon: IconCompass },
    ],
  },
  {
    label: "Acquire",
    items: [
      { to: "/activity", label: "Activity", icon: IconActivity },
      { to: "/indexers", label: "Indexers", icon: IconSearch },
      { to: "/quality", label: "Quality", icon: IconSliders },
      { to: "/watchlist", label: "Watchlist", icon: IconBookmark },
      { to: "/calendar", label: "Calendar", icon: IconCalendar },
    ],
  },
  {
    label: "Maintain",
    items: [
      { to: "/transcode", label: "Transcode", icon: IconConvert },
      { to: "/scripts", label: "Scripts", icon: IconCode },
      { to: "/health", label: "Health", icon: IconHealth },
    ],
  },
  {
    label: "More",
    items: [
      { to: "/chat", label: "Chat", icon: IconChat },
      { to: "/settings", label: "Settings", icon: IconSettings },
    ],
  },
];

// Flat lookup for the topbar title.
const TITLES = {};
NAV_GROUPS.forEach((g) => g.items.forEach((i) => (TITLES[i.to] = i.label)));

function Sidebar({ open, onClose }) {
  const { logout } = useAuth();
  return (
    <nav className={"sidebar" + (open ? " open" : "")}>
      <div className="sidebar-brand">
        <IconLogo size={22} />
        <span>Managarr</span>
      </div>
      {NAV_GROUPS.map((group) => (
        <div className="nav-group" key={group.label}>
          <div className="nav-group-label">{group.label}</div>
          {group.items.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              onClick={onClose}
              className={({ isActive }) => "nav-item" + (isActive ? " active" : "")}
            >
              <Icon size={17} />
              <span>{label}</span>
            </NavLink>
          ))}
        </div>
      ))}
      <div className="sidebar-spacer" />
      <div className="sidebar-footer">
        <button className="nav-item" onClick={() => { onClose && onClose(); logout(); }}>
          <IconLogout size={17} />
          <span>Sign out</span>
        </button>
      </div>
    </nav>
  );
}

function Topbar({ onMenu }) {
  const location = useLocation();
  const { username } = useAuth();
  // Best-effort title: exact match, else first path segment.
  let title = TITLES[location.pathname];
  if (!title) {
    const seg = location.pathname.split("/")[1] || "";
    title = seg ? seg.charAt(0).toUpperCase() + seg.slice(1) : "Managarr";
  }
  return (
    <header className="topbar">
      <button className="topbar-menu" onClick={onMenu} aria-label="Menu">☰</button>
      <div className="topbar-title">{title}</div>
      <GlobalSearch />
      {username && <span className="faint" style={{ fontSize: 13 }}>{username}</span>}
    </header>
  );
}

function AppLayout() {
  const [navOpen, setNavOpen] = useState(false);
  const location = useLocation();
  useEffect(() => { setNavOpen(false); }, [location.pathname]);
  useTvNavFocus(isTvApp);
  return (
    <div className="app-layout">
      <Sidebar open={navOpen} onClose={() => setNavOpen(false)} />
      {navOpen && <div className="sidebar-backdrop" onClick={() => setNavOpen(false)} />}
      <div className="main-content">
        <Topbar onMenu={() => setNavOpen(true)} />
        <div className="page-body">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/search" element={<Navigate to="/" replace />} />
            <Route path="/movies" element={<Movies />} />
            <Route path="/movies/:id" element={<MovieDetail />} />
            <Route path="/tv" element={<TV />} />
            <Route path="/tv/:id" element={<SeriesDetail />} />
            <Route path="/audiobooks" element={<Audiobooks />} />
            <Route path="/audiobooks/:id" element={<AudiobookDetail />} />
            <Route path="/collections" element={<Collections />} />
            <Route path="/collections/:id" element={<CollectionDetail />} />
            <Route path="/discover" element={<Discover />} />
            <Route path="/activity" element={<Activity />} />
            <Route path="/indexers" element={<Indexers />} />
            <Route path="/quality" element={<Quality />} />
            <Route path="/watchlist" element={<Watchlist />} />
            <Route path="/calendar" element={<Calendar />} />
            <Route path="/transcode" element={<Transcode />} />
            <Route path="/scripts" element={<Scripts />} />
            <Route path="/health" element={<Health />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/link" element={<LinkDevice />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  useTvKeyboardGuard(isTvApp);
  return (
    <AuthProvider>
      <ToastProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route
            path="/*"
            element={
              <RequireAuth>
                <AppLayout />
              </RequireAuth>
            }
          />
        </Routes>
      </ToastProvider>
    </AuthProvider>
  );
}
