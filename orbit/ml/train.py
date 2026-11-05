"""Train all models from the warehouse, register artefacts, and write serving tables + metric artefacts.

    python -m orbit.ml.train [--horizons 7 14 28]

All metrics are computed on SIMULATED data (or real M5 when ORBIT_M5_DIR is set) and are tagged with data_kind.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime

import numpy as np

from orbit.config import Settings, get_settings
from orbit.data.world_loader import load_calendar_arrays, load_inventory, load_orders, load_world
from orbit.logging_setup import get_logger, setup_logging
from orbit.ml import forecast as fc
from orbit.ml import stockout as so
from orbit.ml import supplier as sp
from orbit.ml.features import HORIZONS
from orbit.ml.registry import Registry
from orbit.warehouse import db
from orbit.warehouse.serving import SERVING_DDL

log = get_logger("orbit.ml")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _put_metric(conn, key: str, obj) -> None:
    conn.execute("INSERT INTO serving_metrics(key,json,created_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
                 "json=excluded.json, created_at=excluded.created_at", (key, json.dumps(obj, default=float), _now()))
    conn.commit()


def train_all(settings: Settings | None = None, horizons=HORIZONS) -> dict:
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    conn = db.connect(settings.db_path)
    conn.executescript(SERVING_DDL)
    reg = Registry(settings.models_dir)
    world = load_world(conn, settings.seed)
    cal = load_calendar_arrays(conn, world)
    data_kind = db.meta_get(conn, "data_kind", "UNKNOWN")
    dataset_hash = hashlib.sha256(world.D.tobytes() + str(settings.seed).encode()).hexdigest()[:16]
    as_of = str(world.dates[-1].date())
    summary: dict = dict(data_kind=data_kind, dataset_hash=dataset_hash, as_of=as_of, timings={})
    series = world.series
    ids = series[["item_id", "store_id"]]

    # ------------------------------------------------------------ demand forecasting
    t0 = time.perf_counter()
    if horizons:  # an empty tuple skips forecasting (re-uses existing serving_forecast / registry entries)
        conn.execute("DELETE FROM serving_forecast"); conn.execute("DELETE FROM serving_backtest"); conn.commit()
    comparison = {}
    for h in horizons:
        res = fc.run_horizon(world, cal, h, seed=settings.seed)
        model = res.final_models[res.selected] if res.selected in res.final_models else None
        meta = reg.save(f"demand_h{h}", model if model is not None else {"baseline": res.selected},
                        params=dict(selected=res.selected, **fc.MODEL_PARAMS.get(res.selected, {}),
                                    interval_level=fc.INTERVAL_LEVEL, q_norm=res.q_norm),
                        features=["m7", "m14", "m28", "m56", "..."] if model is not None else [],
                        metrics={m: v["test"] for m, v in res.metrics.items()}, dataset_hash=dataset_hash,
                        data_kind=data_kind, extra=dict(q_norm=res.q_norm, coverage_test=res.interval_coverage_test))
        served = fc.serve_forecast(world, cal, model, h, res.q_norm, baseline=res.selected)  # also serves baseline winners
        if served is not None:
            sv = served.merge(ids.reset_index().rename(columns={"index": "series"}), on="series")
            conn.executemany(
                "INSERT INTO serving_forecast VALUES (?,?,?,?,?,?,?,?,?)",
                [(r.item_id, r.store_id, h, as_of, float(r.point), float(r.lo), float(r.hi), res.selected,
                  meta["run_id"]) for r in sv.itertuples()])
        keep = res.backtest[res.backtest.model.isin([res.selected, "seasonal_naive", "moving_average"])]
        bt = keep.merge(ids.reset_index().rename(columns={"index": "series"}), on="series")
        bt["origin_date"] = world.dates[bt.origin.to_numpy()].strftime("%Y-%m-%d")
        conn.executemany("INSERT INTO serving_backtest VALUES (?,?,?,?,?,?,?,?)",
                         list(zip(bt.item_id, bt.store_id, [h] * len(bt), bt.origin_date, bt.model, bt.role,
                                  bt.y.astype(float), bt.pred.astype(float))))
        conn.commit()
        comparison[str(h)] = dict(metrics=res.metrics, selected=res.selected, q_norm=res.q_norm,
                                  interval_level=fc.INTERVAL_LEVEL, interval_coverage_test=res.interval_coverage_test,
                                  residuals=res.residuals, model_run=meta["run_id"],
                                  folds=dict(n_folds=fc.N_FOLDS, fold_weeks=fc.FOLD_WEEKS,
                                             purge_days=h, validation_folds=[0, 1, 2], test_fold=3))
        log.info("forecast horizon done", extra={"horizon": h, "selected": res.selected})
    if horizons:
        _put_metric(conn, "forecast_comparison", dict(data_kind=data_kind, horizons=comparison))
        _put_metric(conn, "seasonality", fc.seasonality_analysis(world, cal))
    summary["timings"]["forecasting_s"] = round(time.perf_counter() - t0, 1)

    # ------------------------------------------------------------ stockout + supplier models
    t0 = time.perf_counter()
    inv = load_inventory(conn, world)
    orders = load_orders(conn, world)
    stats = sp.rolling_supplier_stats(orders[orders.observed].assign(sup=lambda d: d.sup), len(world.sup_ids), world.T)
    pend = so.pending_order_arrays(world, orders)
    origins = np.arange(60, world.T, 3)
    ds = so.build_dataset(world, inv, pend, stats, origins)
    st = so.train_stockout(ds, world.T, seed=settings.seed)
    sel = st["selected"]
    meta_so = reg.save("stockout_risk", st["fitted"][sel],
                       params=dict(selected=sel, horizon_days=so.HORIZON_DAYS, costs=st["costs"]),
                       features=so.FEATURES, metrics=st["models"][sel], dataset_hash=dataset_hash,
                       data_kind=data_kind, extra=dict(threshold=st["models"][sel]["threshold"],
                                                        label="SIMULATION-DERIVED"))
    thr = st["models"][sel]["threshold"]
    # serve: risk at the last day using end-of-history state
    last = so.build_dataset(world, inv, pend, stats, np.array([world.T - 1]))
    p = st["fitted"][sel].predict_proba1(last[so.FEATURES].to_numpy())
    f7 = {(r["item_id"], r["store_id"]): r["point"] for r in conn.execute(
        "SELECT item_id, store_id, point FROM serving_forecast WHERE horizon=7")}
    conn.execute("DELETE FROM serving_stockout_risk")
    rows = []
    for k, r in enumerate(last.itertuples()):
        s = int(r.series); key = (series.item_id[s], series.store_id[s])
        fore = float(f7.get(key, r.m28 * 7))
        avail = r.on_hand + r.due7_promised
        short = max(0.0, fore - avail)
        rows.append((key[0], key[1], as_of, float(p[k]), int(p[k] >= thr), thr, float(r.on_hand), float(r.on_order),
                     float(r.due7_promised), float(r.overdue_qty), float(r.cover_days), float(r.pos_cover_days),
                     float(r.m28), float(r.sup_lead_mean), float(r.sup_late_rate), fore, short,
                     float(short * world.P[s, -1]), meta_so["run_id"]))
    conn.executemany("INSERT INTO serving_stockout_risk VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    so_metrics = {k: v for k, v in st.items() if k != "fitted"}
    _put_metric(conn, "stockout_model", dict(data_kind=data_kind, label="SIMULATION-DERIVED", **so_metrics))

    # supplier risk
    po = sp.build_po_dataset(orders.assign(sup=orders.sup), world, stats)
    sr = sp.train_supplier_risk(po, world.T, seed=settings.seed)
    sel_s = sr["selected"]
    meta_sp = reg.save("supplier_risk", sr["fitted"][sel_s], params=dict(selected=sel_s, late_threshold_days=2),
                       features=sp.SUPPLIER_FEATURES, metrics=sr["models"][sel_s], dataset_hash=dataset_hash,
                       data_kind=data_kind, extra=dict(label="SIMULATION"))
    conn.execute("DELETE FROM serving_supplier_risk")
    t_end = world.T - 1
    rows = []
    for i, sid in enumerate(world.sup_ids):
        m = orders[(orders.sup == i)]
        open_pos = int((m.status == "open").sum())
        overdue = int(((m.status == "open") & (m.order_day + m.promised_lead < t_end)).sum())
        # typical hypothetical order today for this supplier
        typ = po[po.sup == i].tail(50)
        x = typ[sp.SUPPLIER_FEATURES].copy()
        x["late_rate_recent"] = np.nan_to_num(stats["late_rate"][i, t_end], nan=float(typ.late_rate_recent.mean()))
        x["lead_mean_recent"] = np.nan_to_num(stats["lead_mean"][i, t_end], nan=float(typ.lead_mean_recent.mean()))
        x["lead_sd_recent"] = stats["lead_sd"][i, t_end]; x["n_obs_recent"] = stats["n_obs"][i, t_end]
        x["lead_vs_promised"] = x["lead_mean_recent"] - x["promised_lead"]
        pl = float(sr["fitted"][sel_s].predict_proba(x[sp.SUPPLIER_FEATURES].to_numpy())[:, 1].mean())
        lr = float(np.nan_to_num(stats["late_rate"][i, t_end]))
        level = "high" if pl >= 0.30 else ("medium" if pl >= 0.15 else "low")
        rows.append((sid, as_of, lr, float(np.nan_to_num(stats["lead_mean"][i, t_end])),
                     float(stats["lead_sd"][i, t_end]), float(world.sup_mean[i, t_end]), pl, open_pos, overdue,
                     int(stats["n_obs"][i, t_end]), level, meta_sp["run_id"]))
    conn.executemany("INSERT INTO serving_supplier_risk VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    _put_metric(conn, "supplier_model", dict(data_kind=data_kind, label="SIMULATION",
                                             **{k: v for k, v in sr.items() if k != "fitted"}))
    summary["timings"]["stockout_supplier_s"] = round(time.perf_counter() - t0, 1)
    summary["stockout_selected"], summary["supplier_selected"] = sel, sel_s
    if horizons:
        summary["forecast_selected"] = {h: comparison[str(h)]["selected"] for h in horizons}
    db.meta_set(conn, "last_train_at", _now())
    conn.close()
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    (settings.artifacts_dir / "train_summary.json").write_text(json.dumps(summary, indent=2, default=float))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizons", type=int, nargs="*", default=list(HORIZONS),
                    help="forecast horizons to (re)train; pass none to skip forecasting")
    a = ap.parse_args()
    out = train_all(horizons=tuple(a.horizons))
    print(json.dumps(out, indent=2, default=float))


if __name__ == "__main__":
    main()
