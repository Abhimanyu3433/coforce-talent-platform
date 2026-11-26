// Small fetch wrapper: adds the JWT, query params and readable errors.
const BASE = import.meta.env.VITE_API_URL || "";
const TOKEN_KEY = "cf_token";

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

export async function api(path, { method = "GET", body, params } = {}) {
  const url = new URL(BASE + path, window.location.origin);
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "" && !(Array.isArray(v) && v.length === 0)) {
      url.searchParams.set(k, Array.isArray(v) ? v.join(",") : v);
    }
  });
  const token = tokenStore.get();
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (res.status === 401 && !path.endsWith("/auth/login")) {
    tokenStore.clear();
    window.dispatchEvent(new Event("cf-logout"));
  }
  if (!res.ok) {
    const d = data?.detail;
    const msg = typeof d === "string" ? d : d?.message || (Array.isArray(d) ? d.map((x) => x.msg).join("; ") : res.statusText);
    throw new ApiError(msg, res.status, d);
  }
  return data;
}
