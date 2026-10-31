// Minimal fetch wrapper. Base = /api (relative — same origin as the served SPA).
// Attaches Authorization: Bearer <token> from localStorage. On 401 it clears the
// token and redirects to /login.

const TOKEN_KEY = "managarr_token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(t) {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

function buildUrl(path, params) {
  let url = "/api" + (path.startsWith("/") ? path : "/" + path);
  if (params && Object.keys(params).length) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null || v === "") continue;
      qs.append(k, v);
    }
    const s = qs.toString();
    if (s) url += (url.includes("?") ? "&" : "?") + s;
  }
  return url;
}

async function request(method, path, { params, body } = {}) {
  const headers = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const init = { method, headers };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }

  let res;
  try {
    res = await fetch(buildUrl(path, params), init);
  } catch (e) {
    throw new ApiError("Network error — is the backend reachable?", 0);
  }

  if (res.status === 401) {
    setToken(null);
    if (!window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
    }
    throw new ApiError("Unauthorized", 401);
  }

  let data = null;
  const ctype = res.headers.get("content-type") || "";
  if (ctype.includes("application/json")) {
    data = await res.json().catch(() => null);
  } else {
    data = await res.text().catch(() => null);
  }

  if (!res.ok) {
    const detail =
      (data && (data.detail || data.message || data.error)) ||
      (typeof data === "string" && data) ||
      `Request failed (${res.status})`;
    throw new ApiError(detail, res.status, data);
  }
  return data;
}

export class ApiError extends Error {
  constructor(message, status, data) {
    super(typeof message === "string" ? message : "Request failed");
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

export const api = {
  get: (path, params) => request("GET", path, { params }),
  post: (path, body, params) => request("POST", path, { body, params }),
  patch: (path, body, params) => request("PATCH", path, { body, params }),
  put: (path, body, params) => request("PUT", path, { body, params }),
  del: (path, params) => request("DELETE", path, { params }),
};

export default api;
