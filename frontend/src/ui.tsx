import React, { useEffect, useState } from "react";
import { api, ApiError } from "./api";

export function useApi<T = any>(path: string | null, params: Record<string, any> = {}, deps: any[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const key = path + JSON.stringify(params) + JSON.stringify(deps);
  useEffect(() => {
    if (!path) return;
    let live = true; setLoading(true); setError(null);
    api.get<T>(path, params).then(d => live && setData(d)).catch((e: ApiError) => live && (setError(e.message), setData(null))).finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [key]);
  return { data, error, loading };
}

export function State({ loading, error, children, empty }: { loading: boolean; error: string | null; children: React.ReactNode; empty?: boolean }) {
  if (error) return <div className="state err" role="alert">{error}</div>;
  if (loading) return <div className="state" aria-busy="true">Loading…</div>;
  if (empty) return <div className="state">No data for this selection.</div>;
  return <>{children}</>;
}
export const Kpi = ({ label, value, note }: { label: string; value: React.ReactNode; note?: string }) =>
  <div className="card kpi"><div className="l">{label}</div><div className="v">{value}</div>{note && <div className="n">{note}</div>}</div>;
export const Risk = ({ level }: { level: string }) => <span className={`pill p-${level}`}>{level}</span>;
export const n0 = (v: number | null | undefined) => v == null ? "–" : Math.round(v).toLocaleString();
export const n1 = (v: number | null | undefined) => v == null ? "–" : (Math.round(v * 10) / 10).toLocaleString();
export const usd = (v: number | null | undefined) => v == null ? "–" : (v < 0 ? "-$" : "$") + Math.abs(Math.round(v)).toLocaleString();
export const pct = (v: number | null | undefined, d = 0) => v == null ? "–" : (v * 100).toFixed(d) + "%";

export function SeriesPicker({ item, store, setItem, setStore }: { item: string; store: string; setItem: (s: string) => void; setStore: (s: string) => void }) {
  const products = useApi("/products", { limit: 500 });
  const stores = useApi("/stores");
  return (
    <div className="row">
      <label>Product<select value={item} onChange={e => setItem(e.target.value)}>{products.data?.items.map((p: any) => <option key={p.item_id}>{p.item_id}</option>)}</select></label>
      <label>Store<select value={store} onChange={e => setStore(e.target.value)}>{stores.data?.items.map((s: any) => <option key={s.store_id}>{s.store_id}</option>)}</select></label>
    </div>
  );
}
