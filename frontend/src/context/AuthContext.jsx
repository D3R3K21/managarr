import React, { createContext, useContext, useState, useCallback, useEffect } from "react";
import { Navigate, useLocation } from "react-router-dom";
import api, { getToken, setToken } from "../api/client";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [token, setTok] = useState(() => getToken());
  const [username, setUsername] = useState(null);
  const [role, setRole] = useState(null);

  // On reload (token present but no in-memory identity) re-derive who we are.
  useEffect(() => {
    if (token && !role) {
      api.get("/auth/me")
        .then((me) => { setUsername(me.username); setRole(me.role || "user"); })
        .catch(() => {});
    }
  }, [token, role]);

  const login = useCallback(async (user, password) => {
    const data = await api.post("/auth/login", { username: user, password });
    // Backend returns {token, username, role}
    setToken(data.token);
    setTok(data.token);
    setUsername(data.username || user);
    setRole(data.role || "user");
    return data;
  }, []);

  // Set the session directly from an already-issued token (device-pairing flow).
  const applyToken = useCallback((tok, user, r) => {
    setToken(tok);
    setTok(tok);
    setUsername(user || null);
    setRole(r || "user");
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    setTok(null);
    setUsername(null);
    setRole(null);
  }, []);

  const value = {
    token,
    username,
    role,
    isAdmin: role === "admin",
    isAuthenticated: !!token,
    login,
    applyToken,
    logout,
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

export function RequireAuth({ children }) {
  const { isAuthenticated } = useAuth();
  const location = useLocation();
  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }
  return children;
}
