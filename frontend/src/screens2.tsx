import React, { useState } from "react";
import { api } from "./api";
import { HBars, Scatter } from "./charts";
import { Kpi, State, n0, n1, pct, usd, useApi } from "./ui";

const DEFAULTS = { holding_rate_annual: 0.25, order_cost: 12, stockout_cost_multiplier: 1, service_level: 0.95, mode: "cost", batching: true, demand_multiplier: 1, lead_time_add_days: 0 } as any;

function AssumptionForm({ a, set }: { a: any; set: (a: any) => void }) {
  const num = (k: string, label: string, step = 1) => <label key={k}>{label}<input type="number" step={step} value={a[k] ?? ""} onChange={e => set({ ...a, [k]: e.target.value === "" ? undefined : +e.target.value })} style={{ width: 110 }} /></label>;
  return <div className="row">
    <label>Mode<select value={a.mode} onChange={e => set({ ...a, mode: e.target.value })}><option value="cost">Minimise cost</option><option value="service">Target service level</option></select></label>
    {num("service_level", "Service level", 0.01)}{num("stockout_cost_multiplier", "Stockout cost ×", 0.1)}{num("order_cost", "Order cost $")}{num("holding_rate_annual", "Holding rate / yr", 0.01)}
    {num("demand_multiplier", "Demand ×", 0.1)}{num("lead_time_add_days", "Lead time + days")}{num("budget", "Budget $")}{num("supplier_capacity_units", "Supplier cap (units)")}{num("storage_capacity_volume", "Storage cap (vol)")}</div>;
}

export function Planner({ ctx }: { ctx: any }) {
  const stores = useApi("/stores"); const [store, setStore] = useState(ctx.store ?? "CA_1"); const [a, setA] = useState<any>({ ...DEFAULTS });
  const [res, setRes] = useState<any>(null); const [err, setErr] = useState<string | null>(null); const [busy, setBusy] = useState(false); const [sens, setSens] = useState(false);
  const run = async () => { setBusy(true); setErr(null); try { setRes(await api.post("/replenishment/recommend", { store_id: store, assumptions: a, include_sensitivity: sens })); } catch (e: any) { setErr(e.message); setRes(null); } finally { setBusy(false); } };
  const rows = res?.rows?.filter((r: any) => r.qty > 0) ?? [];
  return (
    <section><h2>Replenishment Planner</h2><p className="sub">Constrained, explainable order recommendations. Edit assumptions and re-run.</p>
      <div className="card"><div className="row"><label>Store<select value={store} onChange={e => setStore(e.target.value)}>{stores.data?.items.map((s: any) => <option key={s.store_id}>{s.store_id}</option>)}</select></label>
        <label className="note" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}><input type="checkbox" checked={sens} onChange={e => setSens(e.target.checked)} />Include stockout-cost sensitivity</label></div>
        <AssumptionForm a={a} set={setA} /><button className="btn" onClick={run} disabled={busy}>{busy ? "Optimising…" : "Generate plan"}</button>{" "}<button className="btn ghost" onClick={() => setA({ ...DEFAULTS })}>Reset</button></div>
      {err && <div className="state err" role="alert">{err}</div>}
      {res && <>
        <div className="grid g4" style={{ margin: "14px 0" }}>
          <Kpi label="Items ordered" value={`${res.totals.items_ordered} / ${res.totals.items}`} /><Kpi label="Order spend" value={usd(res.totals.order_spend)} note={`${n0(res.totals.total_qty)} units`} />
          <Kpi label="Expected service level" value={pct(res.totals.expected_service_level, 1)} /><Kpi label="Expected total cost" value={usd(res.totals.expected_cost.total)} note={`hold ${usd(res.totals.expected_cost.holding)} · order ${usd(res.totals.expected_cost.ordering)} · lost ${usd(res.totals.expected_cost.lost_sales)}`} /></div>
        <div className="callout"><b>Solver:</b> {res.method}. <b>Binding constraints:</b> {res.binding_constraints.length ? res.binding_constraints.join(", ") : "none"}. {res.label}</div>
        {res.constraints?.length > 0 && <div className="card" style={{ marginBottom: 14 }}><h3>Constraint utilisation</h3><HBars ariaLabel="Constraint utilisation" unit="%" rows={res.constraints.map((c: any) => ({ label: c.name, value: Math.min(100, (c.utilisation ?? 0) * 100), sub: `${n0(c.used)}/${n0(c.limit)}` }))} /></div>}
        <div className="card"><h3>Recommended orders</h3><div className="scroll"><table><thead><tr><th>Product</th><th>Supplier</th><th className="num">Order qty</th><th className="num">Packs</th><th className="num">On hand</th><th className="num">On order</th><th className="num">Safety stock</th><th className="num">Reorder lvl</th><th className="num">Spend</th><th>Why</th></tr></thead><tbody>
          {rows.map((r: any) => <tr key={r.item_id}><td>{r.item_id}</td><td>{r.supplier_id}</td><td className="num"><b>{n0(r.qty)}</b></td><td className="num">{n0(r.packs)}</td><td className="num">{n0(r.on_hand)}</td><td className="num">{n0(r.on_order)}</td><td className="num">{n0(r.safety_stock)}</td><td className="num">{n0(r.reorder_level)}</td><td className="num">{usd(r.order_spend)}</td><td className="note">{r.reason}</td></tr>)}</tbody></table></div>
          {!rows.length && <p className="note">No orders recommended under these assumptions.</p>}</div>
        {res.sensitivity && <div className="card" style={{ marginTop: 14 }}><h3>Sensitivity to the assumed stockout cost</h3><table><thead><tr>{Object.keys(res.sensitivity[0]).map(k => <th key={k}>{k}</th>)}</tr></thead><tbody>{res.sensitivity.map((r: any, i: number) => <tr key={i}>{Object.values(r).map((v: any, j) => <td key={j}>{typeof v === "number" ? n1(v) : String(v)}</td>)}</tr>)}</tbody></table></div>}</>}
    </section>
  );
}

export function Simulator() {
  const stores = useApi("/stores"); const sim = useApi("/simulation"); const [store, setStore] = useState("CA_1");
  const [scs, setScs] = useState<any[]>([{ name: "Base", assumptions: { ...DEFAULTS } }, { name: "Lead time +7 days", assumptions: { ...DEFAULTS, lead_time_add_days: 7 } }, { name: "Demand +25%", assumptions: { ...DEFAULTS, demand_multiplier: 1.25 } }, { name: "Budget $10k", assumptions: { ...DEFAULTS, budget: 10000 } }]);
  const [res, setRes] = useState<any>(null); const [err, setErr] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  const run = async () => { setBusy(true); setErr(null); try { setRes(await api.post("/scenarios/run", { store_id: store, scenarios: scs })); } catch (e: any) { setErr(e.message); } finally { setBusy(false); } };
  const P = sim.data?.policies; const names: Record<string, string> = { baseline_default: "Incumbent (default)", baseline_tuned: "Incumbent (tuned)", orbit_basestock_v1: "ORBIT v1 base-stock", orbit: "ORBIT", orbit_service_95: "ORBIT service 95%", orbit_ablation_no_forecast_model: "ORBIT, moving-avg forecast" };
  const pts = (sim.data?.baseline_frontier_hindsight ?? []).map((f: any) => ({ x: f.avg_inventory_value, y: f.total_cost, label: `trigger ${f.trigger}, up-to ${f.up_to}` }));
  const orb = P ? [{ x: P.orbit.avg_inventory_value.mean, y: P.orbit.total_cost.mean, label: "ORBIT", hi: true }] : [];
  return (
    <section><h2>Scenario Simulator</h2><p className="sub">What-if analysis, plus the historical policy backtest. Everything here is simulated.</p>
      <div className="card"><h3>What-if scenarios (expected values)</h3><div className="row"><label>Store<select value={store} onChange={e => setStore(e.target.value)}>{stores.data?.items.map((s: any) => <option key={s.store_id}>{s.store_id}</option>)}</select></label><button className="btn" onClick={run} disabled={busy}>{busy ? "Running…" : "Run 4 scenarios"}</button></div>
        {err && <div className="state err" role="alert">{err}</div>}
        {res && <div className="scroll"><table><thead><tr><th>Scenario</th><th className="num">Order qty</th><th className="num">Spend</th><th className="num">Service</th><th className="num">Expected cost</th><th className="num">Δ cost vs first</th><th>Binding</th></tr></thead><tbody>
          {res.scenarios.map((s: any) => s.feasible ? <tr key={s.name}><td>{s.name}</td><td className="num">{n0(s.totals.total_qty)}</td><td className="num">{usd(s.totals.order_spend)}</td><td className="num">{pct(s.totals.expected_service_level, 1)}</td><td className="num">{usd(s.totals.expected_cost.total)}</td><td className="num">{s.delta_vs_first ? usd(s.delta_vs_first.expected_total_cost) : "–"}</td><td>{s.binding_constraints.join(", ") || "–"}</td></tr> : <tr key={s.name}><td>{s.name}</td><td colSpan={6} className="note">Infeasible: {s.error}</td></tr>)}</tbody></table></div>}
        <p className="note">Scenarios re-solve the optimiser under changed demand, lead time, budget or capacity. Costs are model expectations, not forecasts of realised outcomes.</p></div>
      <div className="card" style={{ marginTop: 14 }}><h3>Historical policy backtest</h3><State loading={sim.loading} error={sim.error}>{sim.data && <>
        <div className="callout"><b>{sim.data.label}.</b> Window {sim.data.window[0]} → {sim.data.window[1]}, {sim.data.n_replications} replications that vary supplier randomness only (one demand path). Identical demand, opening inventory and supplier draws for every policy.</div>
        <div className="scroll"><table><thead><tr><th>Policy</th><th className="num">Total cost</th><th className="num">± sd</th><th className="num">Fill rate</th><th className="num">Avg inventory value</th><th className="num">Orders</th></tr></thead><tbody>
          {Object.keys(P).map(k => <tr key={k}><td>{names[k] ?? k}</td><td className="num"><b>{usd(P[k].total_cost.mean)}</b></td><td className="num">{usd(P[k].total_cost.sd)}</td><td className="num">{pct(P[k].service_level_fill_rate.mean, 1)}</td><td className="num">{usd(P[k].avg_inventory_value.mean)}</td><td className="num">{n0(P[k].n_orders.mean)}</td></tr>)}</tbody></table></div>
        <h3 style={{ marginTop: 16 }}>Cost vs working capital: every incumbent configuration (hindsight) vs ORBIT</h3>
        <Scatter ariaLabel="Total cost versus average inventory value" xLabel="Average inventory value ($)" yLabel="Total cost ($)" points={pts.concat(orb)} highlight="ORBIT" />
        <div className="callout"><b>Reading this honestly:</b> ORBIT's gain over the <i>tuned</i> incumbent is small and comes with higher working capital. Replacing the ML forecast with a trailing average changed cost by only {pct(P.orbit_ablation_no_forecast_model.total_cost.mean / P.orbit.total_cost.mean - 1, 1)}. Most of the gain over the <i>default</i> incumbent is retuned cover levels and order batching. ORBIT's EOQ batching was added after seeing the first backtest (v1 is shown); the backtest uses one demand path.</div>
        <p className="note">If ORBIT believes stockouts cost 0.5× the truth, simulated cost is {usd(sim.data.misspecified_stockout_cost.results["0.5"].total_cost)}; at 2× it is {usd(sim.data.misspecified_stockout_cost.results["2.0"].total_cost)} (true-cost evaluation).</p></>}</State></div>
    </section>
  );
}

type Msg = { role: "u" | "a"; text: string; cites?: any[]; meta?: string };
export function Assistant({ ctx }: { ctx: any }) {
  const [msgs, setMsgs] = useState<Msg[]>([]); const [q, setQ] = useState(""); const [busy, setBusy] = useState(false);
  const ask = async (text: string) => {
    if (!text.trim()) return; setMsgs(m => [...m, { role: "u", text }]); setQ(""); setBusy(true);
    try { const r = await api.post("/assistant/ask", { question: text, item_id: ctx.item, store_id: ctx.store }); setMsgs(m => [...m, { role: "a", text: r.answer, cites: r.citations, meta: `${r.provider} · ${r.supported ? "grounded" : "unsupported"}` }]); }
    catch (e: any) { setMsgs(m => [...m, { role: "a", text: "Error: " + e.message }]); } finally { setBusy(false); }
  };
  const ex = ["What needs attention today?", "Which products are at risk of stockout in CA_1?", "Which supplier is riskiest?", "How accurate are the forecasts?", "How much does ORBIT save vs the baseline?", "Forecast for FOODS_1_001 at CA_1"];
  return (
    <section><h2>AI Assistant</h2><p className="sub">Answers are generated from platform data with citations. No LLM is required; it never writes or runs SQL.</p>
      <div className="chips">{ex.map(e => <button key={e} onClick={() => ask(e)}>{e}</button>)}</div>
      <div className="chat" aria-live="polite">{msgs.map((m, i) => <div key={i} className={`msg ${m.role}`}>{m.text}
        {m.cites && m.cites.length > 0 && <div className="cite">Evidence: {m.cites.map(c => `[${c.id}] ${c.title} (${c.source})`).join(" · ")}</div>}{m.meta && <div className="cite">{m.meta}</div>}</div>)}
        {!msgs.length && <div className="state">Ask a question or pick an example.</div>}</div>
      <form className="row" onSubmit={e => { e.preventDefault(); ask(q); }}><label style={{ flex: 1 }}>Question<input value={q} onChange={e => setQ(e.target.value)} maxLength={500} placeholder="e.g. How much should we order of FOODS_3_090 at CA_1?" /></label><button className="btn" disabled={busy}>{busy ? "Thinking…" : "Ask"}</button></form>
    </section>
  );
}
