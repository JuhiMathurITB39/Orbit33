export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}
const KEY = () => { try { return localStorage.getItem("orbit_api_key") || ""; } catch { return ""; } };

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  const k = KEY(); if (k) headers["x-api-key"] = k;
  let r: Response;
  try { r = await fetch("/api/v1" + path, { ...init, headers }); }
  catch { throw new ApiError(0, "network", "Cannot reach the ORBIT API. Is the server running?"); }
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(r.status, body?.error?.code ?? "error", body?.error?.message ?? `HTTP ${r.status}`);
  return body as T;
}
const qs = (o: Record<string, any>) => {
  const p = new URLSearchParams();
  Object.entries(o).forEach(([k, v]) => v !== undefined && v !== null && v !== "" && p.set(k, String(v)));
  const s = p.toString(); return s ? "?" + s : "";
};
export const api = {
  get: <T = any>(path: string, params: Record<string, any> = {}) => call<T>(path + qs(params)),
  post: <T = any>(path: string, body: unknown) => call<T>(path, { method: "POST", body: JSON.stringify(body) }),
};
export type Meta = { data_kind: string; as_of: string; n_products: number; n_stores: number; disclaimer: string };
