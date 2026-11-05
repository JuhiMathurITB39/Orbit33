import React, { useState } from "react";
import { HBars, Legend, LineChart, Bars } from "./charts";
import { Kpi, Risk, SeriesPicker, State, n0, n1, pct, usd, useApi } from "./ui";

export function ControlTower({ go }: { go: (s: string, ctx?: any) => void }) {
  const { data: k, error, loading } = useApi("/kpis");
  return (
    <section><h2>Control Tower</h2><p className="sub">Portfolio status and today's highest-value actions.</p>
      <State loading={loading} error={error}>{k && <>
        <div className="grid g4">
          <Kpi label="Inventory value (at cost)" value={usd(k.total_inventory_value)} note={`${n0(k.total_on_hand_units)} units on hand`} />
          <Kpi label="Series at stockout risk" value={`${k.products_at_risk} / ${k.series_total}`} note={`${pct(k.share_at_risk)} of product-store pairs`} />
          <Kpi label="Estimated exposure" value={usd(k.financial_exposure)} note="model-based, next 7 days" />
          <Kpi label="Next-7-day demand forecast" value={`${n0(k.forecast_7d_units)} units`} note={`≈ ${usd(k.forecast_7d_revenue)} revenue`} />
          <Kpi label="Open purchase orders" value={n0(k.open_purchase_orders)} note={usd(k.open_po_value) + " at cost"} />
          <Kpi label="Trailing 28-day fill rate" value={pct(k.trailing_28d_fill_rate, 1)} note="current incumbent policy" />
          <Kpi label="Suppliers by risk" value={<>{k.supplier_risk.high} <Risk level="high" /> {k.supplier_risk.medium} <Risk level="medium" /></>} note={`${k.supplier_risk.low} low`} />
        </div>
        <div className="card" style={{ marginTop: 14 }}><h3>Priority actions</h3>
          <div className="scroll"><table><thead><tr><th>Product</th><th>Store</th><th className="num">Stockout prob.</th><th className="num">On hand</th><th className="num">Cover (d)</th><th className="num">Est. exposure</th><th className="num">Suggested order</th><th>Why</th></tr></thead>
            <tbody>{k.recommended_actions.map((a: any) => (
              <tr key={a.item_id + a.store_id} className="click" tabIndex={0} onClick={() => go("planner", { item: a.item_id, store: a.store_id })} onKeyDown={e => e.key === "Enter" && go("planner", { item: a.item_id, store: a.store_id })}>
                <td>{a.item_id}</td><td>{a.store_id}</td><td className="num">{pct(a.prob)}</td><td className="num">{n0(a.on_hand)}</td><td className="num">{n1(a.cover_days)}</td><td className="num">{usd(a.expected_loss)}</td><td className="num">{n0(a.recommended_qty)}</td><td className="note">{a.reason}</td></tr>))}</tbody></table></div>
          <p className="note">{k.notes[0]}</p></div></>}</State></section>
  );
}

export function Demand({ ctx }: { ctx: any }) {
  const [item, setItem] = useState(ctx.item ?? "FOODS_1_001"); const [store, setStore] = useState(ctx.store ?? "CA_1"); const [h, setH] = useState(7);
  const hist = useApi("/sales/history", { item_id: item, store_id: store, granularity: "week" });
  const fc = useApi("/forecast", { item_id: item, store_id: store });
  const bt = useApi("/forecast/backtest", { item_id: item, store_id: store, horizon: h });
  const seas = useApi("/demand/seasonality", { item_id: item, store_id: store });
  const models = useApi("/models/comparison");
  const pts = hist.data?.points ?? []; const last = pts.slice(-52);
  const cmp = models.data?.forecast?.horizons?.[String(h)];
  const names = cmp ? Object.keys(cmp.metrics) : [];
  return (
    <section><h2>Demand Intelligence</h2><p className="sub">Weekly history, forecasts with uncertainty, and honest model comparison.</p>
      <SeriesPicker item={item} store={store} setItem={setItem} setStore={setStore} />
      <div className="grid g2">
        <div className="card"><h3>Weekly units sold (last 52 weeks)</h3><State loading={hist.loading} error={hist.error} empty={!last.length}>
          <LineChart ariaLabel="Weekly units sold" x={last.map((p: any) => p.date)} series={[{ name: "Units", values: last.map((p: any) => p.units) }]} /></State></div>
        <div className="card"><h3>Forecast (total units over next N days)</h3><State loading={fc.loading} error={fc.error}>{fc.data && <>
          <table><thead><tr><th>Horizon</th><th className="num">Point</th><th className="num">80% interval</th><th>Model</th></tr></thead><tbody>
            {fc.data.horizons.map((r: any) => <tr key={r.horizon}><td>{r.horizon} days</td><td className="num">{n0(r.point)}</td><td className="num">{n0(r.lo)} – {n0(r.hi)}</td><td>{r.model}</td></tr>)}</tbody></table>
          <p className="note">{fc.data.note}</p></>}</State></div>
        <div className="card"><h3>Backtest: actual vs predicted ({h}-day)</h3>
          <div className="row"><label>Horizon<select value={h} onChange={e => setH(+e.target.value)}><option value={7}>7 days</option><option value={14}>14 days</option><option value={28}>28 days</option></select></label></div>
          <State loading={bt.loading} error={bt.error} empty={!bt.data?.points.length}>{bt.data && (() => { const p = bt.data.points; const sel = Object.keys(p[0]).find(k => !["origin_date", "actual", "role", "seasonal_naive", "moving_average"].includes(k)) ?? "";
            return <LineChart ariaLabel="Backtest actual vs predicted" x={p.map((r: any) => r.origin_date)} series={[{ name: "Actual", values: p.map((r: any) => r.actual) }, { name: sel, values: p.map((r: any) => r[sel] ?? null) }, { name: "seasonal naive", values: p.map((r: any) => r.seasonal_naive ?? null), dashed: true }]} />; })()}</State></div>
        <div className="card"><h3>Seasonality</h3><State loading={seas.loading} error={seas.error}>{seas.data && <>
          <p className="note">Average units by day of week (last 2 years)</p>
          <Bars ariaLabel="Average units by day of week" labels={seas.data.day_of_week.map((d: any) => d.label)} values={seas.data.day_of_week.map((d: any) => d.avg_units)} height={150} /></>}</State></div>
      </div>
      <div className="card" style={{ marginTop: 14 }}><h3>Model comparison — {h}-day horizon, rolling-origin backtest</h3>
        <State loading={models.loading} error={models.error} empty={!cmp}>{cmp && <>
          <div className="scroll"><table><thead><tr><th>Model</th><th className="num">Validation WAPE</th><th className="num">Test WAPE</th><th className="num">Test MAE</th><th className="num">Test bias</th></tr></thead><tbody>
            {names.map(m => { const v = cmp.metrics[m]; return <tr key={m}><td>{m}{m === cmp.selected && <> <span className="pill p-low">selected</span></>}</td><td className="num">{pct(v.validation?.wape, 1)}</td><td className="num">{pct(v.test?.wape, 1)}</td><td className="num">{n1(v.test?.mae)}</td><td className="num">{pct(v.test?.bias, 1)}</td></tr>; })}</tbody></table></div>
          <p className="note">Selection: simplest model within 2% of best validation WAPE. 80% interval test coverage: {pct(cmp.interval_coverage_test, 1)}. Folds: {cmp.folds.n_folds} × {cmp.folds.fold_weeks} weeks, purge gap = horizon. All results are on simulated data.</p></>}</State></div>
    </section>
  );
}

export function Inventory({ go }: { go: (s: string, ctx?: any) => void }) {
  const [store, setStore] = useState(""); const [q, setQ] = useState(""); const [page, setPage] = useState(0); const [view, setView] = useState<"inv" | "risk">("risk");
  const stores = useApi("/stores");
  const inv = useApi("/inventory", { store_id: store, q, limit: 25, offset: page * 25 }, [view]);
  const risk = useApi("/stockout-risk", { store_id: store, limit: 25, offset: page * 25, sort: "revenue_at_risk", min_prob: 0.1 }, [view]);
  const cur = view === "inv" ? inv : risk;
  return (
    <section><h2>Inventory</h2><p className="sub">Positions, reorder points and stockout risk. Inventory is simulated.</p>
      <div className="row">
        <label>View<select value={view} onChange={e => { setView(e.target.value as any); setPage(0); }}><option value="risk">Stockout risk</option><option value="inv">Positions &amp; reorder points</option></select></label>
        <label>Store<select value={store} onChange={e => { setStore(e.target.value); setPage(0); }}><option value="">All</option>{stores.data?.items.map((s: any) => <option key={s.store_id}>{s.store_id}</option>)}</select></label>
        {view === "inv" && <label>Search product<input value={q} onChange={e => { setQ(e.target.value); setPage(0); }} placeholder="e.g. FOODS_3" /></label>}
      </div>
      <div className="card"><State loading={cur.loading} error={cur.error} empty={!cur.data?.items.length}>{cur.data && <>
        <div className="scroll">{view === "inv" ?
          <table><thead><tr><th>Product</th><th>Store</th><th className="num">On hand</th><th className="num">On order</th><th className="num">Overdue</th><th className="num">Safety stock</th><th className="num">Reorder pt</th><th className="num">Cover (d)</th><th className="num">Stockout prob.</th></tr></thead><tbody>
            {cur.data.items.map((r: any) => <tr key={r.item_id + r.store_id} className="click" onClick={() => go("planner", { item: r.item_id, store: r.store_id })}><td>{r.item_id}</td><td>{r.store_id}</td><td className="num">{n0(r.on_hand)}</td><td className="num">{n0(r.on_order)}</td><td className="num">{n0(r.overdue_qty)}</td><td className="num" title={r.ss_method}>{n0(r.safety_stock)}</td><td className="num">{n0(r.reorder_point)}</td><td className="num">{n1(r.cover_days)}</td><td className="num">{pct(r.prob)}</td></tr>)}</tbody></table> :
          <table><thead><tr><th>Product</th><th>Store</th><th className="num">Probability</th><th className="num">On hand</th><th className="num">Forecast 7d</th><th className="num">Exposure</th><th>Supplier</th><th className="num">Supplier late rate</th></tr></thead><tbody>
            {cur.data.items.map((r: any) => <tr key={r.item_id + r.store_id} className="click" onClick={() => go("planner", { item: r.item_id, store: r.store_id })}><td>{r.item_id}</td><td>{r.store_id}</td><td className="num">{pct(r.prob)}</td><td className="num">{n0(r.on_hand)}</td><td className="num">{n0(r.forecast7)}</td><td className="num">{usd(r.expected_loss)}</td><td>{r.supplier_id}</td><td className="num">{pct(r.sup_late_rate)}</td></tr>)}</tbody></table>}</div>
        <div className="row" style={{ marginTop: 10 }}><button className="btn ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
          <span className="note">{cur.data.offset + 1}–{Math.min(cur.data.offset + 25, cur.data.total)} of {cur.data.total}</span>
          <button className="btn ghost" disabled={(page + 1) * 25 >= cur.data.total} onClick={() => setPage(page + 1)}>Next</button></div>
        {view === "inv" && <p className="note">{cur.data.note}</p>}</>}</State></div>
    </section>
  );
}

export function Suppliers() {
  const list = useApi("/suppliers"); const [sel, setSel] = useState("S05");
  const prof = useApi("/suppliers/" + sel);
  return (
    <section><h2>Supplier Intelligence</h2><p className="sub">Reliability and lead-time risk. All suppliers are synthetic.</p>
      <div className="card"><State loading={list.loading} error={list.error}>{list.data && <>
        <div className="scroll"><table><thead><tr><th>Supplier</th><th>Risk</th><th className="num">Contract lead (d)</th><th className="num">Recent lead (d)</th><th className="num">Recent late rate</th><th className="num">Predicted late prob.</th><th className="num">Open POs</th><th className="num">Overdue</th></tr></thead><tbody>
          {list.data.items.map((s: any) => <tr key={s.supplier_id} className="click" tabIndex={0} aria-selected={s.supplier_id === sel} onClick={() => setSel(s.supplier_id)} onKeyDown={e => e.key === "Enter" && setSel(s.supplier_id)}>
            <td><b>{s.supplier_id}</b></td><td><Risk level={s.risk_level} /></td><td className="num">{n1(s.contract_lead_days)}</td><td className="num">{n1(s.recent_lead_mean)}</td><td className="num">{pct(s.recent_late_rate)}</td><td className="num">{pct(s.pred_late_prob)}</td><td className="num">{s.open_pos}</td><td className="num">{s.overdue_pos}</td></tr>)}</tbody></table></div>
        <p className="note">{list.data.note}</p></>}</State></div>
      <div className="grid g2" style={{ marginTop: 14 }}>
        <div className="card"><h3>{sel}: monthly average lead time (days)</h3><State loading={prof.loading} error={prof.error} empty={!prof.data?.monthly.length}>{prof.data && <>
          <LineChart ariaLabel="Monthly lead time" x={prof.data.monthly.map((m: any) => m.month)} series={[{ name: "Avg lead", values: prof.data.monthly.map((m: any) => m.avg_lead) }]} />
          <div className="callout">Terms history (SCD2): {prof.data.terms_history.map((v: any) => `v${v.version} ${v.valid_from.slice(0, 4) === "1900" ? "start" : v.valid_from}: ${v.mean_lead_days}d lead, ${pct(v.on_time_prob)} on-time`).join("  →  ")}</div></>}</State></div>
        <div className="card"><h3>{sel}: monthly late-delivery rate</h3><State loading={prof.loading} error={prof.error} empty={!prof.data?.monthly.length}>{prof.data &&
          <Bars ariaLabel="Monthly late rate" labels={prof.data.monthly.filter((_: any, i: number) => i % 6 === 0).map((m: any) => m.month.slice(2))} values={prof.data.monthly.filter((_: any, i: number) => i % 6 === 0).map((m: any) => (m.late_rate ?? 0) * 100)} unit="%" />}</State>
          <p className="note">Late = delivered 2+ days after the promised date. Predictive model skill is modest (see Model Card).</p></div>
      </div>
    </section>
  );
}
