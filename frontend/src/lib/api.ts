// Small fetch wrapper for the backend API. The token lives in localStorage so a page
// reload keeps you signed in; every storage access is guarded (private mode, blocked storage).

const TOKEN_KEY = "leaddesk.token";
let memoryToken: string | null = null;

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? memoryToken;
  } catch {
    return memoryToken;
  }
}

export function setToken(token: string | null): void {
  memoryToken = token;
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: keep the in-memory copy */
  }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type Unauthorized = () => void;
let onUnauthorized: Unauthorized = () => {};
export function setUnauthorizedHandler(fn: Unauthorized): void {
  onUnauthorized = fn;
}

/** Turns FastAPI error bodies ({detail: "..."} or {detail: [{msg, loc}]}) into one readable line. */
export function errorText(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) => {
          const item = d as { msg?: string; loc?: unknown[] };
          const field = Array.isArray(item.loc) ? item.loc.filter((x) => x !== "body").join(".") : "";
          const msg = (item.msg ?? "").replace(/^Value error, /, "");
          return field ? `${field}: ${msg}` : msg;
        })
        .join("; ");
    }
  }
  return fallback;
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  let resp: Response;
  try {
    resp = await fetch(path, { ...init, headers, body });
  } catch {
    throw new ApiError(0, "Can't reach the server. Check that it is running.");
  }
  if (resp.status === 401) {
    onUnauthorized();
    throw new ApiError(401, "Your session has ended. Sign in again.");
  }
  if (resp.status === 204) return undefined as T;
  const data = await resp.json().catch(() => null);
  if (!resp.ok) throw new ApiError(resp.status, errorText(data, `Request failed (${resp.status})`));
  return data as T;
}

export async function login(email: string, password: string): Promise<string> {
  const form = new URLSearchParams({ username: email, password });
  let resp: Response;
  try {
    resp = await fetch("/api/auth/login", { method: "POST", body: form });
  } catch {
    throw new ApiError(0, "Can't reach the server. Check that it is running.");
  }
  const data = await resp.json().catch(() => null);
  if (!resp.ok) throw new ApiError(resp.status, errorText(data, "Sign-in failed"));
  return (data as { access_token: string }).access_token;
}
