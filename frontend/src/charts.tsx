import React from "react";

const PAL = ["#2f6fed", "#e07a1f", "#12936b", "#b4469e", "#6b7280"];
export const color = (i: number) => PAL[i % PAL.length];
const fmt = (v: number) => Math.abs(v) >= 1000 ? (v / 1000).toFixed(v >= 10000 ? 0 : 1) + "k" : (Math.round(v * 10) / 10).toString();

function nice(min: number, max: number, n = 4) {
  if (min === max) { max = min + 1; }
  const step = Math.pow(10, Math.floor(Math.log10((max - min) / n)));
  const err = (max - min) / n / step; const m = err >= 5 ? 5 : err >= 2 ? 2 : 1;
  const s = m * step; return { lo: Math.floor(min / s) * s, hi: Math.ceil(max / s) * s, ticks: Array.from({ length: Math.round((Math.ceil(max / s) * s - Math.floor(min / s) * s) / s) + 1 }, (_, i) => Math.floor(min / s) * s + i * s) };
}

export type Series = { name: string; values: (number | null)[]; dashed?: boolean };
type LineProps = { x: string[]; series: Series[]; band?: { lo: (number | null)[]; hi: (number | null)[] }; height?: number; yLabel?: string; ariaLabel: string };

export function LineChart({ x, series, band, height = 240, yLabel, ariaLabel }: LineProps) {
  const W = 720, H = height, L = 46, R = 10, T = 10, B = 26;
  const all = series.flatMap(s => s.values).concat(band ? band.lo.concat(band.hi) : []).filter((v): v is number => v !== null);
  if (!all.length) return <div className="empty">No data</div>;
  const { lo, hi, ticks } = nice(Math.min(0, ...all), Math.max(...all));
  const px = (i: number) => L + (x.length <= 1 ? 0 : (i / (x.length - 1)) * (W - L - R));
  const py = (v: number) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const path = (vals: (number | null)[]) => vals.map((v, i) => v === null ? "" : `${i && vals[i - 1] !== null ? "L" : "M"}${px(i).toFixed(1)},${py(v).toFixed(1)}`).join("");
  const bandPath = band ? band.hi.map((v, i) => `${i ? "L" : "M"}${px(i)},${py(v ?? 0)}`).join("") + band.lo.map((v, i) => `L${px(band.lo.length - 1 - i)},${py(band.lo[band.lo.length - 1 - i] ?? 0)}`).join("") + "Z" : "";
  const xt = x.length > 1 ? [0, Math.floor(x.length / 3), Math.floor((2 * x.length) / 3), x.length - 1] : [0];
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={ariaLabel} preserveAspectRatio="xMidYMid meet">
        {ticks.map(t => <g key={t}><line x1={L} x2={W - R} y1={py(t)} y2={py(t)} className="grid" /><text x={L - 6} y={py(t) + 3} className="tick" textAnchor="end">{fmt(t)}</text></g>)}
        {band && <path d={bandPath} className="band" />}
        {series.map((s, i) => <path key={s.name} d={path(s.values)} fill="none" stroke={color(i)} strokeWidth={2} strokeDasharray={s.dashed ? "5 4" : undefined} />)}
        {xt.map(i => <text key={i} x={px(i)} y={H - 8} className="tick" textAnchor={i === 0 ? "start" : i === x.length - 1 ? "end" : "middle"}>{x[i]}</text>)}
        {yLabel && <text x={4} y={10} className="tick">{yLabel}</text>}
      </svg>
      <Legend names={series.map(s => s.name)} extra={band ? "80% interval" : undefined} />
    </figure>
  );
}

export function Legend({ names, extra }: { names: string[]; extra?: string }) {
  return <div className="legend">{names.map((n, i) => <span key={n}><i style={{ background: color(i) }} />{n}</span>)}{extra && <span><i className="bandkey" />{extra}</span>}</div>;
}

export function Bars({ labels, values, height = 180, ariaLabel, unit = "", colors }: { labels: string[]; values: number[]; height?: number; ariaLabel: string; unit?: string; colors?: string[] }) {
  const W = 720, H = height, L = 46, B = 24, T = 10;
  const max = Math.max(...values, 1); const { hi, ticks } = nice(0, max);
  const bw = (W - L - 10) / values.length;
  return (
    <figure className="chart"><svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={ariaLabel}>
      {ticks.map(t => { const y = T + (1 - t / hi) * (H - T - B); return <g key={t}><line x1={L} x2={W - 10} y1={y} y2={y} className="grid" /><text x={L - 6} y={y + 3} className="tick" textAnchor="end">{fmt(t)}{unit}</text></g>; })}
      {values.map((v, i) => { const h = (v / hi) * (H - T - B); return <g key={i}><rect x={L + i * bw + bw * 0.15} y={H - B - h} width={bw * 0.7} height={Math.max(h, 0)} rx={2} fill={colors?.[i] ?? color(0)} /><text x={L + i * bw + bw / 2} y={H - 8} className="tick" textAnchor="middle">{labels[i]}</text></g>; })}
    </svg></figure>
  );
}

export function HBars({ rows, ariaLabel, unit = "" }: { rows: { label: string; value: number; sub?: string; color?: string }[]; ariaLabel: string; unit?: string }) {
  const max = Math.max(...rows.map(r => r.value), 1e-9);
  return (
    <div role="img" aria-label={ariaLabel} className="hbars">
      {rows.map((r, i) => (
        <div key={r.label} className="hrow"><span className="hl">{r.label}</span>
          <span className="hb"><span style={{ width: `${(r.value / max) * 100}%`, background: r.color ?? color(i) }} /></span>
          <span className="hv">{unit === "$" ? "$" : ""}{fmt(r.value)}{unit === "$" ? "" : unit}{r.sub ? <em> {r.sub}</em> : null}</span></div>
      ))}
    </div>
  );
}

export function Scatter({ points, ariaLabel, xLabel, yLabel, highlight }: { points: { x: number; y: number; label?: string; hi?: boolean }[]; ariaLabel: string; xLabel: string; yLabel: string; highlight?: string }) {
  const W = 720, H = 300, L = 54, B = 34, T = 10, R = 12;
  if (!points.length) return <div className="empty">No data</div>;
  const X = nice(Math.min(...points.map(p => p.x)), Math.max(...points.map(p => p.x))), Y = nice(Math.min(...points.map(p => p.y)), Math.max(...points.map(p => p.y)));
  const px = (v: number) => L + ((v - X.lo) / (X.hi - X.lo)) * (W - L - R), py = (v: number) => T + (1 - (v - Y.lo) / (Y.hi - Y.lo)) * (H - T - B);
  return (
    <figure className="chart"><svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={ariaLabel}>
      {Y.ticks.map(t => <g key={t}><line x1={L} x2={W - R} y1={py(t)} y2={py(t)} className="grid" /><text x={L - 6} y={py(t) + 3} className="tick" textAnchor="end">{fmt(t)}</text></g>)}
      {X.ticks.map(t => <text key={t} x={px(t)} y={H - 18} className="tick" textAnchor="middle">{fmt(t)}</text>)}
      {points.map((p, i) => <circle key={i} cx={px(p.x)} cy={py(p.y)} r={p.hi ? 6 : 3.5} fill={p.hi ? color(1) : color(0)} fillOpacity={p.hi ? 1 : 0.55}><title>{p.label}</title></circle>)}
      <text x={(L + W) / 2} y={H - 3} className="tick" textAnchor="middle">{xLabel}</text><text x={4} y={H - 3} className="tick">{yLabel}</text>
    </svg><figcaption className="legend"><span><i style={{ background: color(0) }} />Baseline configurations (hindsight)</span><span><i style={{ background: color(1) }} />{highlight}</span><span hidden>Baseline configurations (hindsight)</span></figcaption></figure>
  );
}
