import { config } from "@/lib/config";
import { clearToken, getToken } from "@/lib/auth";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** On an expired/invalid session (401 while we DO hold a token), drop it and
 *  bounce to the login page. Login-page failures (no token held) just throw. */
function handle401() {
  if (typeof window === "undefined") return;
  if (!getToken()) return;
  clearToken();
  const base = process.env.NEXT_PUBLIC_BASE_PATH || "";
  if (!window.location.pathname.endsWith("/login")) window.location.href = `${base}/login`;
}

/**
 * Build a request URL. When `apiBase` is set (Mac dev -> http://localhost:8000)
 * we produce an absolute URL. When it's empty (production behind nginx) we use
 * a same-origin relative URL so requests hit internet.acuronai.com/... which
 * nginx proxies to the FastAPI backend.
 */
function buildUrl(path: string, params?: Record<string, string | number | undefined>): string {
  const qs = new URLSearchParams();
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== "") qs.set(k, String(v));
    }
  }
  const query = qs.toString() ? `?${qs}` : "";
  if (config.apiBase) return new URL(path + query, config.apiBase).toString();
  return path + query; // relative, same-origin
}

/** Thin typed fetch wrapper around the FastAPI backend. */
export async function apiGet<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const res = await fetch(buildUrl(path, params), {
    headers: { Accept: "application/json", ...authHeaders() },
    cache: "no-store",
  });
  if (res.status === 401) handle401();
  if (!res.ok) throw new ApiError(res.status, `GET ${path} failed (${res.status})`);
  return (await res.json()) as T;
}

export async function apiSend<T>(method: "POST" | "PUT" | "PATCH" | "DELETE", path: string, body?: unknown): Promise<T> {
  const res = await fetch(buildUrl(path), {
    method,
    headers: { "Content-Type": "application/json", Accept: "application/json", ...authHeaders() },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) handle401();
  if (!res.ok) {
    let detail = "";
    try { detail = ((await res.json()) as { detail?: string }).detail ?? ""; } catch { /* ignore */ }
    throw new ApiError(res.status, detail || `${method} ${path} failed (${res.status})`);
  }
  return (await res.json()) as T;
}
