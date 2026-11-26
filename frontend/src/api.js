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

// Fetch a protected file (e.g. a certificate proof) as a Blob — the JWT can't ride along on a plain <a href>.
export async function apiBlob(path) {
  const token = tokenStore.get();
  const res = await fetch(BASE + path, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    throw new ApiError(typeof data?.detail === "string" ? data.detail : res.statusText, res.status, data?.detail);
  }
  return res.blob();
}

export const MAX_UPLOAD_MB = 5;

// Read a chosen file as base64 (without the data: prefix), checking extension and size first.
export function readFileBase64(file, { exts = [], label = "file" } = {}) {
  return new Promise((resolve, reject) => {
    const name = (file?.name || "").toLowerCase();
    if (!file) return reject(new Error("No file chosen."));
    if (exts.length && !exts.some((x) => name.endsWith(x))) return reject(new Error(`Please choose a ${label} (${exts.join(", ")}).`));
    if (file.size > MAX_UPLOAD_MB * 1048576)
      return reject(new Error(`That file is ${(file.size / 1048576).toFixed(1)} MB — the limit is ${MAX_UPLOAD_MB} MB.`));
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",")[1] || "");
    r.onerror = () => reject(new Error("The file could not be read."));
    r.readAsDataURL(file);
  });
}
