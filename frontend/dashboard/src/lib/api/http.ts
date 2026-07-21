import { config } from "@/lib/config";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
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
  const res = await fetch(buildUrl(path, params), { headers: { Accept: "application/json" }, cache: "no-store" });
  if (!res.ok) throw new ApiError(res.status, `GET ${path} failed (${res.status})`);
  return (await res.json()) as T;
}

export async function apiSend<T>(method: "POST" | "PUT" | "PATCH" | "DELETE", path: string, body?: unknown): Promise<T> {
  const res = await fetch(buildUrl(path), {
    method,
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new ApiError(res.status, `${method} ${path} failed (${res.status})`);
  return (await res.json()) as T;
}
