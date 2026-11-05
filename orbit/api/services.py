"""API business logic. All SQL uses bound parameters; the database is opened read-only per request."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import numpy as np
import pandas as pd

from orbit.config import Settings
from orbit.data.supply_sim import World
from orbit.data.world_loader import DataNotReadyError, load_world
from orbit.optim.replenish import InfeasiblePlanError, Items, OptParams, cost_sensitivity, optimise
from orbit.optim.safety_stock import reorder_point, safety_stock
from orbit.warehouse import db

LONG_LEAD_CUTOFF = 17.5
LOST_GOODWILL = 0.25


class NotFoundError(LookupError):
    pass


class ServiceUnavailable(RuntimeError):
    pass


class BadRequest(ValueError):
    pass


def rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


class Runtime:
    """Process-wide lazily-built caches (world matrices, position table). Rebuilt only on process restart."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._world: World | None = None
        self._position: pd.DataFrame | None = None
        self._plans: dict = {}

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        if not self.settings.db_path.exists():
            raise ServiceUnavailable("the warehouse is not available; run the data pipeline first")
        try:
            conn = db.connect(self.settings.db_path, readonly=True)
        except sqlite3.Error as exc:
            raise ServiceUnavailable("the warehouse cannot be opened") from exc
        try:
            yield conn
        except sqlite3.OperationalError as exc:
            raise ServiceUnavailable("the warehouse is not ready (missing tables); run the pipeline and training") from exc
        finally:
            conn.close()

    def world(self) -> World:
        with self._lock:
            if self._world is None:
                with self.session() as c:
                    try:
                        self._world = load_world(c, self.settings.seed)
                    except DataNotReadyError as exc:
                        raise ServiceUnavailable(str(exc)) from exc
            return self._world

    def meta(self) -> dict:
        with self.session() as c:
            kind = db.meta_get(c, "data_kind", "UNKNOWN")
            as_of = c.execute("SELECT MAX(as_of_date) FROM serving_forecast").fetchone()[0]
            n_p = c.execute("SELECT COUNT(*) FROM dim_product").fetchone()[0]
            n_s = c.execute("SELECT COUNT(*) FROM dim_store").fetchone()[0]
            trained = db.meta_get(c, "last_train_at")
        return dict(data_kind=kind, as_of=as_of, n_products=n_p, n_stores=n_s, last_train_at=trained,
                    disclaimer=("All demand, inventory, supplier and cost figures are SIMULATED (M5-schema simulator) "
                                "unless data_kind is REAL_M5; inventory, suppliers and purchase orders are always synthetic."
                                if kind != "REAL_M5" else
                                "Demand is real M5 data; inventory, suppliers and purchase orders are synthetic."))

    # ---------------------------------------------------------------- position table
    def position(self) -> pd.DataFrame:
        with self._lock:
            if self._position is not None:
                return self._position
        w = self.world()
        with self.session() as c:
            pos = pd.DataFrame(rows(c, """
                SELECT r.item_id, r.store_id, r.prob, r.flagged, r.threshold, r.on_hand, r.on_order, r.due7_promised,
                       r.overdue_qty, r.cover_days, r.pos_cover_days, r.m28, r.sup_late_rate, r.forecast7,
                       p.dept_id, p.cat_id, p.supplier_id, p.unit_cost, p.ref_price, p.pack_size, p.moq_units, p.unit_volume
                FROM serving_stockout_risk r JOIN dim_product p ON p.item_id = r.item_id"""))
            fc = pd.DataFrame(rows(c, "SELECT item_id, store_id, horizon, point, lo, hi FROM serving_forecast"))
            sup = pd.DataFrame(rows(c, "SELECT supplier_id, recent_lead_mean, recent_lead_sd, contract_lead_mean, "
                                       "risk_level, pred_late_prob FROM serving_supplier_risk"))
        if pos.empty or fc.empty:
            raise ServiceUnavailable("model outputs are not available; run `python -m orbit.ml.train`")
        piv = fc.pivot_table(index=["item_id", "store_id"], columns="horizon", values=["point", "lo", "hi"])
        piv.columns = [f"{a}{b}" for a, b in piv.columns]
        pos = pos.merge(piv.reset_index(), on=["item_id", "store_id"], how="left").merge(sup, on="supplier_id", how="left")
        pos["lead_mean"] = np.where(pos.recent_lead_mean > 0, pos.recent_lead_mean, pos.contract_lead_mean)
        pos["lead_sd"] = np.where(pos.recent_lead_sd > 0, pos.recent_lead_sd, 0.25 * pos.lead_mean)
        protect = pos.lead_mean + 7.0
        pos["mu_d"] = np.where(protect > LONG_LEAD_CUTOFF, pos.point28 / 28.0, pos.point14 / 14.0)
        idx = {(i, s): n for n, (i, s) in enumerate(zip(w.series.item_id, w.series.store_id))}
        sidx = np.array([idx[(i, s)] for i, s in zip(pos.item_id, pos.store_id)])
        pos["series"] = sidx
        pos["sigma_d"] = w.D[sidx, -56:].std(axis=1, ddof=1)
        ss = safety_stock(pos.mu_d.to_numpy(), pos.sigma_d.to_numpy(), pos.lead_mean.to_numpy(), pos.lead_sd.to_numpy(),
                          7.0, 0.95)
        rp = reorder_point(pos.mu_d.to_numpy(), pos.sigma_d.to_numpy(), pos.lead_mean.to_numpy(),
                           pos.lead_sd.to_numpy(), 0.95)
        pos["safety_stock"], pos["ss_method"], pos["reorder_point"] = ss["ss"], ss["method"], rp["rop"]
        pos["inventory_value"] = pos.on_hand * pos.unit_cost
        pos["lost_unit_cost"] = (pos.ref_price - pos.unit_cost).clip(lower=0) + LOST_GOODWILL * pos.ref_price
        pos["exposure_units"] = (pos.forecast7 - pos.on_hand).clip(lower=0)
        pos["expected_loss"] = pos.prob * pos.exposure_units * pos.lost_unit_cost
        pos["store_idx"] = pos.store_id.map({s: i for i, s in enumerate(w.store_ids)})
        pos["supplier_idx"] = pos.supplier_id.map({s: i for i, s in enumerate(w.sup_ids)})
        with self._lock:
            self._position = pos
        return pos


# ------------------------------------------------------------------------------------------ simple queries
def search_products(rt: Runtime, q: str | None, cat_id: str | None, limit: int, offset: int) -> dict:
    where, params = ["1=1"], []
    if q:
        where.append("(item_id LIKE ? ESCAPE '\\' OR dept_id LIKE ? ESCAPE '\\')")
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        params += [like, like]
    if cat_id:
        where.append("cat_id = ?"); params.append(cat_id)
    with rt.session() as c:
        total = c.execute(f"SELECT COUNT(*) FROM dim_product WHERE {' AND '.join(where)}", params).fetchone()[0]  # noqa: S608
        items = rows(c, f"SELECT item_id, dept_id, cat_id, supplier_id, unit_cost, ref_price, pack_size, moq_units "  # noqa: S608
                        f"FROM dim_product WHERE {' AND '.join(where)} ORDER BY item_id LIMIT ? OFFSET ?", (*params, limit, offset))
    return dict(items=items, total=total, limit=limit, offset=offset)


def list_stores(rt: Runtime) -> dict:
    with rt.session() as c:
        items = rows(c, """SELECT s.store_id, s.state_id, s.storage_capacity_volume, w.warehouse_id, w.name AS warehouse_name
                           FROM dim_store s JOIN dim_warehouse w USING(warehouse_key) ORDER BY s.store_id""")
    return dict(items=items, total=len(items), limit=len(items), offset=0)


def _require_series(c: sqlite3.Connection, item_id: str, store_id: str) -> None:
    ok = c.execute("SELECT 1 FROM dim_product p, dim_store s WHERE p.item_id=? AND s.store_id=?", (item_id, store_id)).fetchone()
    if not ok:
        raise NotFoundError(f"unknown product/store: {item_id} / {store_id}")


def sales_history(rt: Runtime, item_id: str, store_id: str, start: str | None, end: str | None, gran: str) -> dict:
    with rt.session() as c:
        _require_series(c, item_id, store_id)
        base = """FROM fact_sales f JOIN dim_product p USING(product_key) JOIN dim_store s USING(store_key)
                  JOIN dim_date d USING(date_key) WHERE p.item_id=? AND s.store_id=?"""
        params: list = [item_id, store_id]
        if start:
            base += " AND d.date >= ?"; params.append(start)
        if end:
            base += " AND d.date <= ?"; params.append(end)
        if gran == "week":
            data = rows(c, f"SELECT MIN(d.date) AS date, SUM(f.units_sold) AS units, AVG(f.sell_price) AS avg_price "  # noqa: S608
                           f"{base} GROUP BY d.wm_yr_wk ORDER BY MIN(d.date)", tuple(params))
        else:
            data = rows(c, f"SELECT d.date AS date, f.units_sold AS units, f.sell_price AS avg_price {base} "  # noqa: S608
                           f"ORDER BY d.date", tuple(params))
    return dict(item_id=item_id, store_id=store_id, granularity=gran, points=data)


def forecast_for(rt: Runtime, item_id: str, store_id: str) -> dict:
    with rt.session() as c:
        _require_series(c, item_id, store_id)
        f = rows(c, "SELECT horizon, point, lo, hi, model, as_of_date FROM serving_forecast WHERE item_id=? AND store_id=? "
                    "ORDER BY horizon", (item_id, store_id))
        cmp_ = c.execute("SELECT json FROM serving_metrics WHERE key='forecast_comparison'").fetchone()
    if not f:
        raise NotFoundError("no forecast available for this series")
    level = None
    if cmp_:
        level = json.loads(cmp_[0])["horizons"]["7"]["interval_level"]
    return dict(item_id=item_id, store_id=store_id, as_of=f[0]["as_of_date"], interval_level=level,
                horizons=[{k: v for k, v in r.items() if k != "as_of_date"} for r in f],
                note="Forecast = total units over the next N days. Interval is a split-conformal interval calibrated on "
                     "validation folds of simulated/historical data; it assumes the error distribution is stable over time.")


def forecast_backtest(rt: Runtime, item_id: str, store_id: str, horizon: int) -> dict:
    with rt.session() as c:
        _require_series(c, item_id, store_id)
        data = rows(c, "SELECT origin_date, model, role, y, pred FROM serving_backtest WHERE item_id=? AND store_id=? "
                       "AND horizon=? ORDER BY origin_date", (item_id, store_id, horizon))
    piv: dict = {}
    for r in data:
        d = piv.setdefault(r["origin_date"], dict(origin_date=r["origin_date"], actual=r["y"], role=r["role"]))
        d[r["model"]] = r["pred"]
    return dict(item_id=item_id, store_id=store_id, horizon=horizon, points=list(piv.values()))


def model_comparison(rt: Runtime) -> dict:
    with rt.session() as c:
        out = {r["key"]: json.loads(r["json"]) for r in c.execute("SELECT key, json FROM serving_metrics")}
    if "forecast_comparison" not in out:
        raise ServiceUnavailable("model metrics are not available; run training")
    reg = Registry_list(rt)
    return dict(forecast=out["forecast_comparison"], stockout=_trim(out.get("stockout_model")),
                supplier=_trim(out.get("supplier_model")), seasonality=out.get("seasonality"), registry=reg)


def _trim(d: dict | None) -> dict | None:
    return d


def Registry_list(rt: Runtime) -> list[dict]:
    from orbit.ml.registry import Registry
    try:
        return [{k: m[k] for k in ("run_id", "name", "version", "created_at", "dataset_hash", "data_kind", "sha256")}
                for m in Registry(rt.settings.models_dir).list()]
    except Exception:  # registry is informational
        return []


def series_seasonality(rt: Runtime, item_id: str, store_id: str) -> dict:
    with rt.session() as c:
        _require_series(c, item_id, store_id)
        dow = rows(c, """SELECT d.wday AS wday, AVG(f.units_sold) AS avg_units FROM fact_sales f JOIN dim_product p USING(product_key)
                         JOIN dim_store s USING(store_key) JOIN dim_date d USING(date_key)
                         WHERE p.item_id=? AND s.store_id=? AND d.d_idx >= (SELECT MAX(d_idx)-728 FROM dim_date)
                         GROUP BY d.wday ORDER BY d.wday""", (item_id, store_id))
        mon = rows(c, """SELECT d.month AS month, AVG(f.units_sold) AS avg_units FROM fact_sales f JOIN dim_product p USING(product_key)
                         JOIN dim_store s USING(store_key) JOIN dim_date d USING(date_key)
                         WHERE p.item_id=? AND s.store_id=? GROUP BY d.month ORDER BY d.month""", (item_id, store_id))
        cat = c.execute("SELECT json FROM serving_metrics WHERE key='seasonality'").fetchone()
    names = {1: "Sat", 2: "Sun", 3: "Mon", 4: "Tue", 5: "Wed", 6: "Thu", 7: "Fri"}
    for d in dow:
        d["label"] = names[d["wday"]]
    return dict(item_id=item_id, store_id=store_id, day_of_week=dow, month=mon,
                portfolio=json.loads(cat[0]) if cat else None)


# ------------------------------------------------------------------------------------------ risk / inventory
def stockout_risk(rt: Runtime, store_id: str | None, min_prob: float, sort: str, limit: int, offset: int) -> dict:
    pos = rt.position()
    df = pos if not store_id else pos[pos.store_id == store_id]
    df = df[df.prob >= min_prob]
    key = {"prob": "prob", "revenue_at_risk": "expected_loss", "cover_days": "cover_days"}[sort]
    df = df.sort_values(key, ascending=(sort == "cover_days"))
    cols = ["item_id", "store_id", "cat_id", "prob", "flagged", "threshold", "on_hand", "on_order", "due7_promised",
            "overdue_qty", "cover_days", "forecast7", "exposure_units", "expected_loss", "supplier_id", "sup_late_rate"]
    return dict(items=_records(df[cols].iloc[offset: offset + limit]), total=int(len(df)), limit=limit, offset=offset)


def _records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.replace({np.nan: None}).to_json(orient="records"))


def inventory(rt: Runtime, store_id: str | None, item_id: str | None, q: str | None, limit: int, offset: int) -> dict:
    pos = rt.position()
    df = pos
    if store_id:
        df = df[df.store_id == store_id]
    if item_id:
        df = df[df.item_id == item_id]
    if q:
        df = df[df.item_id.str.contains(q, case=False, regex=False)]
    df = df.sort_values(["store_id", "item_id"])
    cols = ["item_id", "store_id", "cat_id", "supplier_id", "on_hand", "on_order", "due7_promised", "overdue_qty",
            "safety_stock", "reorder_point", "ss_method", "inventory_value", "cover_days", "prob", "flagged", "forecast7"]
    out = df[cols].iloc[offset: offset + limit].copy()
    out["available"] = out.on_hand
    out["incoming"] = out.on_order
    return dict(items=_records(out), total=int(len(df)), limit=limit, offset=offset,
                note="reorder_point and safety_stock use a 95% service level and the trailing 56-day demand sd; "
                     "ss_method shows whether the Normal formula was valid or the Gamma quantile was used.")


def _supplier_list(c: sqlite3.Connection) -> list[dict]:
    return rows(c, """SELECT s.supplier_id, s.name, s.mean_lead_days AS contract_lead_days, s.lead_sd_days, s.on_time_prob AS contract_on_time_prob,
                             s.weekly_capacity_units, s.version AS current_version,
                             r.recent_late_rate, r.recent_lead_mean, r.recent_lead_sd, r.pred_late_prob, r.open_pos,
                             r.overdue_pos, r.risk_level, r.n_obs
                      FROM dim_supplier s LEFT JOIN serving_supplier_risk r ON r.supplier_id = s.supplier_id
                      WHERE s.is_current = 1 ORDER BY s.supplier_id""")


def suppliers(rt: Runtime) -> dict:
    with rt.session() as c:
        items = _supplier_list(c)
    return dict(items=items, total=len(items), limit=len(items), offset=0,
                note="SIMULATED suppliers: terms are assumptions and delivery behaviour is generated, not observed.")


def supplier_profile(rt: Runtime, supplier_id: str) -> dict:
    with rt.session() as c:
        sup = [s for s in _supplier_list(c) if s["supplier_id"] == supplier_id]
        if not sup:
            raise NotFoundError(f"unknown supplier {supplier_id}")
        versions = rows(c, "SELECT version, valid_from, valid_to, mean_lead_days, lead_sd_days, on_time_prob FROM dim_supplier "
                           "WHERE supplier_id=? ORDER BY version", (supplier_id,))
        monthly = rows(c, """SELECT substr(d.date,1,7) AS month, COUNT(*) AS orders, AVG(po.actual_lead_days) AS avg_lead,
                                    AVG(po.is_late) AS late_rate
                             FROM fact_purchase_orders po JOIN dim_supplier su USING(supplier_key)
                             JOIN dim_date d ON d.date_key = po.received_date_key
                             WHERE su.supplier_id=? AND po.status != 'open' GROUP BY 1 ORDER BY 1""", (supplier_id,))
        status = rows(c, """SELECT po.status, COUNT(*) AS n FROM fact_purchase_orders po JOIN dim_supplier su USING(supplier_key)
                            WHERE su.supplier_id=? GROUP BY po.status""", (supplier_id,))
    return dict(supplier=sup[0], terms_history=versions, monthly=monthly, po_status=status)


def purchase_orders(rt: Runtime, status: str | None, supplier_id: str | None, limit: int, offset: int) -> dict:
    where, params = ["1=1"], []
    if status:
        where.append("po.status = ?"); params.append(status)
    if supplier_id:
        where.append("su.supplier_id = ?"); params.append(supplier_id)
    w = " AND ".join(where)
    with rt.session() as c:
        total = c.execute(f"SELECT COUNT(*) FROM fact_purchase_orders po JOIN dim_supplier su USING(supplier_key) WHERE {w}", params).fetchone()[0]  # noqa: S608
        items = rows(c, f"""SELECT po.po_id, p.item_id, s.store_id, su.supplier_id, od.date AS order_date, pd_.date AS promised_date,
                                   rd.date AS received_date, po.qty_ordered, po.qty_received, po.status, po.delay_days, po.is_late
                            FROM fact_purchase_orders po JOIN dim_product p USING(product_key) JOIN dim_store s USING(store_key)
                            JOIN dim_supplier su USING(supplier_key) JOIN dim_date od ON od.date_key = po.order_date_key
                            LEFT JOIN dim_date pd_ ON pd_.date_key = po.promised_date_key
                            LEFT JOIN dim_date rd ON rd.date_key = po.received_date_key
                            WHERE {w} ORDER BY po.order_date_key DESC, po.po_id DESC LIMIT ? OFFSET ?""",  # noqa: S608
                     (*params, limit, offset))
    return dict(items=items, total=total, limit=limit, offset=offset)


# ------------------------------------------------------------------------------------------ optimisation
def _build_items(rt: Runtime, store_id: str, item_ids: list[str] | None, a) -> tuple[Items, pd.DataFrame]:
    pos = rt.position()
    st = pos[pos.store_id == store_id]
    if st.empty:
        raise NotFoundError(f"unknown store {store_id}")
    sel = st if not item_ids else st[st.item_id.isin(item_ids)]
    if sel.empty:
        raise BadRequest("none of the requested items exist in this store")
    sel = sel.sort_values("item_id").reset_index(drop=True)
    price = sel.ref_price.to_numpy()
    it = Items(mu_d=(sel.mu_d.to_numpy() * a.demand_multiplier), sigma_d=sel.sigma_d.to_numpy() * a.demand_multiplier,
               lead_mean=np.maximum(sel.lead_mean.to_numpy() + a.lead_time_add_days, 1.0),
               lead_sd=sel.lead_sd.to_numpy(), on_hand=sel.on_hand.to_numpy(), on_order=sel.on_order.to_numpy(),
               unit_cost=sel.unit_cost.to_numpy(), price=price, pack=sel.pack_size.to_numpy().astype(float),
               moq=sel.moq_units.to_numpy().astype(float), vol=sel.unit_volume.to_numpy(),
               supplier=sel.supplier_idx.to_numpy().astype(int), store=np.zeros(len(sel), int),
               ids=sel[["item_id", "store_id", "supplier_id", "cat_id"]])
    return it, sel


def _params(rt: Runtime, a, store_id: str, selected: pd.DataFrame) -> OptParams:
    pos = rt.position()
    with rt.session() as c:
        sup_cap = {r["supplier_id"]: r["weekly_capacity_units"] for r in c.execute(
            "SELECT supplier_id, weekly_capacity_units FROM dim_supplier WHERE is_current=1")}
        store_cap = c.execute("SELECT storage_capacity_volume FROM dim_store WHERE store_id=?", (store_id,)).fetchone()[0]
    cap = a.storage_capacity_volume if a.storage_capacity_volume is not None else store_cap
    others = pos[(pos.store_id == store_id) & (~pos.item_id.isin(selected.item_id))]
    cap_eff = cap - float((others.unit_volume * (others.on_hand + others.on_order)).sum())  # space used by items not in plan
    sup_idx = {s: i for i, s in enumerate(selected.supplier_id.unique())}
    supplier_capacity = {int(selected.supplier_idx[selected.supplier_id == s].iloc[0]):
                         float(a.supplier_capacity_units if a.supplier_capacity_units is not None else sup_cap[s])
                         for s in sup_idx}
    return OptParams(review_days=a.review_days, holding_rate_annual=a.holding_rate_annual, order_cost=a.order_cost,
                     goodwill=a.goodwill, stockout_cost_multiplier=a.stockout_cost_multiplier, mode=a.mode,
                     service_level=a.service_level, batching=a.batching, budget=a.budget,
                     supplier_capacity=supplier_capacity, storage_capacity={0: float(cap_eff)})


def _plan_payload(plan, sel: pd.DataFrame) -> dict:
    df = plan.to_frame()
    df["reason"] = [_reason(r, p) for r, p in zip(df.to_dict("records"), plan.items.on_hand + plan.items.on_order)]
    cost = {k: float(plan.cost[k].sum()) for k in ("holding", "ordering", "lost_sales", "total")}
    dem = np.maximum(plan.cost["demand_mean"], 1e-9)
    return dict(
        rows=_records(df), method=plan.method, binding_constraints=plan.binding, constraints=plan.constraints,
        totals=dict(items=int(len(df)), items_ordered=int((plan.Q > 0).sum()), total_qty=float(plan.Q.sum()),
                    order_spend=float((plan.Q * plan.items.unit_cost).sum()),
                    expected_service_level=float(np.average(plan.cost["service_level"], weights=dem)),
                    expected_shortfall_units=float(plan.cost["expected_shortfall"].sum()), expected_cost=cost),
    )


def _reason(r: dict, pos: float) -> str:
    if r["qty"] <= 0:
        return (f"No order: inventory position {pos:.0f} is at or above the reorder level "
                f"{r['reorder_level']:.0f} (or the optimiser found an order uneconomic under the current constraints).")
    return (f"Order {r['qty']:.0f} units ({r['packs']:.0f} packs): position {pos:.0f} is below the reorder level "
            f"{r['reorder_level']:.0f}; the batch of {r['eoq_batch']:.0f} units (EOQ) amortises the order cost.")


def recommend(rt: Runtime, req) -> dict:
    a = req.assumptions
    it, sel = _build_items(rt, req.store_id, req.item_ids, a)
    p = _params(rt, a, req.store_id, sel)
    try:
        plan = optimise(it, p, service_for_ss=a.service_level)
    except InfeasiblePlanError as exc:
        raise BadRequest(f"no feasible plan under these constraints: {exc}") from exc
    out = _plan_payload(plan, sel)
    out.update(store_id=req.store_id, as_of=rt.meta()["as_of"], assumptions=a.model_dump(),
               label="Model-based recommendation on simulated inventory; not a commercial commitment.")
    if req.include_sensitivity:
        out["sensitivity"] = cost_sensitivity(it, p)
    return out


def run_scenarios(rt: Runtime, req) -> dict:
    results = []
    for sc in req.scenarios:
        a = sc.assumptions
        it, sel = _build_items(rt, req.store_id, req.item_ids, a)
        p = _params(rt, a, req.store_id, sel)
        try:
            plan = optimise(it, p, service_for_ss=a.service_level)
            payload = _plan_payload(plan, sel)
            results.append(dict(name=sc.name, feasible=True, assumptions=a.model_dump(), method=payload["method"],
                                totals=payload["totals"], binding_constraints=payload["binding_constraints"]))
        except InfeasiblePlanError as exc:
            results.append(dict(name=sc.name, feasible=False, assumptions=a.model_dump(), error=str(exc)[:200]))
    base = next((r for r in results if r["feasible"]), None)
    for r in results:
        if r["feasible"] and base:
            r["delta_vs_first"] = dict(
                total_qty=r["totals"]["total_qty"] - base["totals"]["total_qty"],
                order_spend=r["totals"]["order_spend"] - base["totals"]["order_spend"],
                expected_total_cost=r["totals"]["expected_cost"]["total"] - base["totals"]["expected_cost"]["total"])
    return dict(store_id=req.store_id, scenarios=results,
                label="Expected values under the stated assumptions (Gamma demand model); SIMULATED data.")


# ------------------------------------------------------------------------------------------ KPIs / simulation
def kpis(rt: Runtime) -> dict:
    pos = rt.position()
    meta = rt.meta()
    with rt.session() as c:
        sup = _supplier_list(c)
        open_pos = c.execute("SELECT COUNT(*), COALESCE(SUM(qty_ordered*unit_cost),0) FROM fact_purchase_orders WHERE status='open'").fetchone()
        fill = c.execute("""SELECT SUM(units_fulfilled)*1.0/NULLIF(SUM(units_demanded),0) FROM fact_inventory
                            WHERE date_key IN (SELECT date_key FROM dim_date ORDER BY d_idx DESC LIMIT 28)""").fetchone()[0]
    cat_cache = _store_plans(rt)
    top = pos.sort_values("expected_loss", ascending=False).head(8)
    actions = []
    for r in top.itertuples():
        rec = cat_cache.get((r.store_id, r.item_id))
        actions.append(dict(item_id=r.item_id, store_id=r.store_id, prob=float(r.prob), expected_loss=float(r.expected_loss),
                            on_hand=float(r.on_hand), cover_days=float(r.cover_days),
                            recommended_qty=float(rec["qty"]) if rec else None,
                            action=("Place order" if rec and rec["qty"] > 0 else "Expedite / review supplier"),
                            reason=(rec or {}).get("reason")))
    return dict(
        as_of=meta["as_of"], data_kind=meta["data_kind"],
        total_inventory_value=float(pos.inventory_value.sum()), total_on_hand_units=float(pos.on_hand.sum()),
        forecast_7d_units=float(pos.point7.sum()), forecast_28d_units=float(pos.point28.sum()),
        forecast_7d_revenue=float((pos.point7 * pos.ref_price).sum()),
        products_at_risk=int(pos.flagged.sum()), series_total=int(len(pos)),
        share_at_risk=float(pos.flagged.mean()), financial_exposure=float(pos.expected_loss.sum()),
        supplier_risk=dict(high=sum(1 for s in sup if s["risk_level"] == "high"),
                           medium=sum(1 for s in sup if s["risk_level"] == "medium"),
                           low=sum(1 for s in sup if s["risk_level"] == "low")),
        open_purchase_orders=int(open_pos[0]), open_po_value=float(open_pos[1]),
        trailing_28d_fill_rate=float(fill) if fill is not None else None,
        recommended_actions=actions,
        notes=["financial_exposure = sum over products of P(stockout) x (forecast 7d demand - on-hand) x unit lost-sales cost; "
               "a model-based estimate on simulated inventory."])


def _store_plans(rt: Runtime) -> dict:
    """Default-assumption plans per store (cached) used to attach recommended quantities to priority actions."""
    from orbit.api.schemas import Assumptions
    out: dict = {}
    pos = rt.position()
    for store in pos.store_id.unique():
        if store not in rt._plans:
            a = Assumptions()
            it, sel = _build_items(rt, store, None, a)
            try:
                plan = optimise(it, _params(rt, a, store, sel), service_for_ss=a.service_level)
                df = plan.to_frame()
                df["reason"] = [_reason(r, p) for r, p in zip(df.to_dict("records"), it.on_hand + it.on_order)]
                rt._plans[store] = {(store, r["item_id"]): r for r in df.to_dict("records")}
            except InfeasiblePlanError:
                rt._plans[store] = {}
        out.update(rt._plans[store])
    return out


def simulation_results(rt: Runtime) -> dict:
    with rt.session() as c:
        r = c.execute("SELECT json FROM serving_metrics WHERE key='simulation'").fetchone()
    if not r:
        raise ServiceUnavailable("simulation results are not available; run `python -m orbit.optim.run_backtest`")
    return json.loads(r[0])


def health(rt: Runtime) -> dict:
    status = dict(status="ok", database="ok", models="ok")
    try:
        with rt.session() as c:
            c.execute("SELECT 1 FROM dim_product LIMIT 1").fetchone()
            n = c.execute("SELECT COUNT(*) FROM serving_forecast").fetchone()[0]
            if n == 0:
                status.update(status="degraded", models="unavailable")
    except ServiceUnavailable:
        status.update(status="degraded", database="unavailable", models="unknown")
    return status
