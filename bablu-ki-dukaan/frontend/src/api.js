const BASE = import.meta.env.VITE_API_URL || "http://localhost:8000/api";
const KEY = "bablu_token";

// The token lives in sessionStorage: it disappears when the tab/browser is closed.
let token = sessionStorage.getItem(KEY) || "";
let onExpired = () => {};

export const session = {
  get token() { return token; },
  set(t) { token = t; sessionStorage.setItem(KEY, t); },
  clear() { token = ""; sessionStorage.removeItem(KEY); },
  onExpired(fn) { onExpired = fn; },
};

async function request(path, options = {}) {
  const isForm = options.body instanceof FormData;
  const headers = isForm ? {} : { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Token ${token}`;

  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...options,
      headers,
      body: isForm ? options.body : options.body ? JSON.stringify(options.body) : undefined,
    });
  } catch {
    throw new Error("Cannot reach the server. Is Django running?");
  }

  const data = res.status === 204 ? null : await res.json().catch(() => null);

  if (res.status === 401 && token) {          // token expired or was revoked
    session.clear();
    onExpired();
    throw new Error("Session expired. Please log in again.");
  }
  if (!res.ok) {
    const msg =
      data?.detail ||
      (data && Object.entries(data).map(([k, v]) => `${k}: ${[].concat(v).join(" ")}`).join(" | ")) ||
      "Something went wrong";
    throw new Error(Array.isArray(msg) ? msg.join(" ") : msg);
  }
  return data;
}

export const api = {
  get: (p) => request(p),
  post: (p, body) => request(p, { method: "POST", body }),
  patch: (p, body) => request(p, { method: "PATCH", body }),
  del: (p) => request(p, { method: "DELETE" }),
  upload: (p, formData) => request(p, { method: "POST", body: formData }),
};

export const rupees = (n) =>
  "₹" + Number(n || 0).toLocaleString("en-IN", { maximumFractionDigits: 2 });
