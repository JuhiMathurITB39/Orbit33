"""Historical policy backtest: incumbent baseline(s) vs ORBIT, on identical demand, identical supplier randomness
(common random numbers) and identical starting inventory. All outputs are SIMULATED backtest results.

Information discipline (no look-ahead):
  * The forecast models used by ORBIT are RE-TRAINED here using only data up to the backtest start T0.
  * At every decision day t the ORBIT policy builds features from a copy of the world truncated to days <= t.
  * Supplier lead-time estimates come only from deliveries that had already arrived by t.
  * The tuned baseline's parameters are chosen on the 365 days BEFORE T0.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

import numpy as np

from orbit.data.supply_sim import (
    REVIEW_PERIOD,
    BaselinePolicy,
    PolicyContext,
    World,
    economics,
    run_engine,
    with_crn_seed,
)
from orbit.logging_setup import get_logger
from orbit.ml import forecast as fc
from orbit.ml.features import build_features, origins_for
from orbit.optim.replenish import InfeasiblePlanError, Items, OptParams, optimise

log = get_logger("orbit.sim")


@dataclass
class ForecastProvider:
    """Demand forecast (mean per day) for every series at decision day t, from models trained before T0."""
    models: dict          # {14: model, 28: model}
    cal: dict
    cache: dict = field(default_factory=dict)

    def mu_daily(self, ctx: PolicyContext) -> tuple[np.ndarray, np.ndarray]:
        t = ctx.t
        if t not in self.cache:
            w_t = dataclasses.replace(ctx.world, D=ctx.hist_D, P=ctx.hist_P)  # history through day t only
            out = {}
            for h, mdl in self.models.items():
                X = build_features(w_t, self.cal, np.array([t]), h, with_target=False)
                out[h] = mdl.predict(X) / h
            sigma = ctx.hist_D[:, -56:].std(axis=1, ddof=1)
            self.cache[t] = (out, sigma)
        return self.cache[t]


@dataclass
class MovingAverageProvider:
    """Ablation: replace the trained forecast with the trailing 28-day mean (same interface, no model)."""

    def mu_daily(self, ctx: PolicyContext) -> tuple[np.ndarray, np.ndarray]:
        m28 = ctx.hist_D[:, -28:].mean(axis=1)
        return {14: m28, 28: m28}, ctx.hist_D[:, -56:].std(axis=1, ddof=1)


@dataclass
class OrbitPolicy:
    provider: object  # ForecastProvider or MovingAverageProvider
    params: OptParams
    long_lead_cutoff: float = 17.5  # use the 28-day model when protection period exceeds this many days

    def __call__(self, ctx: PolicyContext) -> np.ndarray:
        w = ctx.world
        idx = np.where(ctx.review_mask)[0]
        q = np.zeros(w.S)
        if len(idx) == 0:
            return q
        mus, sigma = self.provider.mu_daily(ctx)
        sup = w.sup_idx[idx]
        lead_mean, lead_sd = ctx.lead_mean[sup], ctx.lead_sd[sup]
        protect = lead_mean + self.params.review_days
        mu = np.where(protect > self.long_lead_cutoff, mus[28][idx], mus[14][idx])
        price = np.where(ctx.hist_P[idx, -1] > 0, ctx.hist_P[idx, -1], w.series.ref_price.to_numpy()[idx])
        stores = np.unique(w.store_idx[idx])
        p = dataclasses.replace(
            self.params,
            storage_capacity={int(k): float(w.store_capacity[k]) for k in stores},
            supplier_capacity={int(s): float(w.sup_capacity[s, ctx.t]) for s in np.unique(sup)},
        )
        items = Items(mu_d=mu, sigma_d=sigma[idx], lead_mean=lead_mean, lead_sd=lead_sd, on_hand=ctx.on_hand[idx],
                      on_order=ctx.on_order[idx], unit_cost=w.unit_cost[idx], price=price, pack=w.pack[idx].astype(float),
                      moq=w.moq[idx].astype(float), vol=w.vol[idx], supplier=sup, store=w.store_idx[idx])
        try:
            q[idx] = optimise(items, p).Q
        except InfeasiblePlanError as exc:  # never crash a simulation; skip ordering today and log it
            log.warning("optimiser infeasible; no order", extra={"day": ctx.t, "error": str(exc)[:120]})
        return q


def train_models_before(world: World, cal: dict, cutoff: int, family: str = "random_forest", seed: int = 42,
                        horizons=(14, 28)) -> dict:
    """Fit the forecasting model for each horizon using ONLY days < cutoff."""
    w_cut = dataclasses.replace(world, D=world.D[:, :cutoff], P=world.P[:, :cutoff])
    models = {}
    for h in horizons:
        data = build_features(w_cut, cal, origins_for(w_cut, h), h)  # targets fully inside [0, cutoff)
        assert data.origin.max() + h <= cutoff - 1
        models[h] = fc.make_model(family, seed).fit(data, data["y"].to_numpy(), data["scale"].to_numpy())
    return models


def tune_baseline(world: World, t_end: int, window: int = 365) -> tuple[BaselinePolicy, list[dict]]:
    """Grid-search the days-of-cover rule on the `window` days BEFORE t_end (leakage-free for a backtest at t_end)."""
    rows = []
    for trig in range(8, 33, 2):
        for gap in (7, 14, 21, 28, 35):
            pol = BaselinePolicy(float(trig), float(trig + gap))
            res = run_engine(world, pol, 0, t_end, first_review_day=7)
            e = economics(world, res, start=t_end - window, end=t_end)
            rows.append(dict(trigger=trig, up_to=trig + gap, total_cost=e["total_cost"],
                             fill_rate=e["service_level_fill_rate"]))
    best = min(rows, key=lambda r: r["total_cost"])
    return BaselinePolicy(float(best["trigger"]), float(best["up_to"])), rows


METRICS = ("total_cost", "holding_cost", "ordering_cost", "lost_sales_cost", "service_level_fill_rate",
           "stockout_day_rate", "avg_inventory_units", "avg_inventory_value", "n_orders", "lost_units")


def run_backtest(world: World, cal: dict, t0: int, base_policy: BaselinePolicy, n_rep: int = 5, seed: int = 42,
                 family: str = "random_forest", sensitivity: bool = True) -> dict:
    assert 700 < t0 < world.T - 30, "backtest window must lie inside history with enough prior data"
    models = train_models_before(world, cal, t0, family, seed)
    provider = ForecastProvider(models, cal)
    c = world.costs
    base_p = OptParams(review_days=REVIEW_PERIOD, holding_rate_annual=c.holding_rate_annual,
                       order_cost=c.order_cost, goodwill=c.goodwill)
    tuned, grid = tune_baseline(world, t0)
    start_state = run_engine(world, base_policy, 0, t0, first_review_day=7).state
    policies = {
        "baseline_default": base_policy,
        "baseline_tuned": tuned,
        "orbit_basestock_v1": OrbitPolicy(provider, dataclasses.replace(base_p, batching=False)),
        "orbit": OrbitPolicy(provider, base_p),
        "orbit_service_95": OrbitPolicy(provider, dataclasses.replace(base_p, mode="service", service_level=0.95)),
        "orbit_ablation_no_forecast_model": OrbitPolicy(MovingAverageProvider(), base_p),
    }
    cats = world.series.cat_id.to_numpy()
    per_rep: dict = {k: [] for k in policies}
    by_cat: dict = {}
    for rep in range(n_rep):
        w_rep = with_crn_seed(world, seed + 1000 + rep)
        for name, pol in policies.items():
            res = run_engine(w_rep, pol, t0, world.T, state=start_state, first_review_day=t0)
            per_rep[name].append(economics(w_rep, res))
            if rep == 0:
                by_cat[name] = {cat: economics(w_rep, res, mask=(cats == cat)) for cat in np.unique(cats)}
        log.info("backtest replication done", extra={"rep": rep})
    agg = {}
    for name, rows in per_rep.items():
        agg[name] = {m: dict(mean=float(np.mean([r[m] for r in rows])), sd=float(np.std([r[m] for r in rows], ddof=1)
                                                                                    if len(rows) > 1 else 0.0))
                     for m in METRICS}
    paired = {}
    for ref in ("baseline_default", "baseline_tuned"):
        for name in ("orbit_basestock_v1", "orbit", "orbit_service_95", "orbit_ablation_no_forecast_model"):
            d = np.array([o["total_cost"] - b["total_cost"] for o, b in zip(per_rep[name], per_rep[ref])])
            rel = np.array([(o["total_cost"] - b["total_cost"]) / b["total_cost"]
                            for o, b in zip(per_rep[name], per_rep[ref])])
            paired[f"{name}_vs_{ref}"] = dict(mean_cost_diff=float(d.mean()), mean_rel_diff=float(rel.mean()),
                                              sd_rel_diff=float(rel.std(ddof=1)) if len(rel) > 1 else 0.0,
                                              share_reps_orbit_cheaper=float((d < 0).mean()))
    out = dict(
        label="SIMULATED BACKTEST - not realised commercial savings", t0=t0, days=world.T - t0,
        window=[str(world.dates[t0].date()), str(world.dates[-1].date())], n_replications=n_rep,
        forecast_family_trained_before_t0=family, tuned_baseline=dict(trigger=tuned.trigger_days, up_to=tuned.up_to_days),
        baseline_grid=grid, policies=agg, paired=paired, by_category_rep0=by_cat,
        cost_params=dataclasses.asdict(c),
        assumptions=["identical demand history, supplier draws and opening inventory for every policy",
                     "lost sales are not back-ordered", "M5 units treated as uncensored demand",
                     "ORBIT optimises the same cost function the evaluation measures; baselines do not"],
    )
    # Hindsight frontier: every baseline configuration evaluated ON the test window (generous to the baseline, since a
    # real planner could not pick the best configuration in advance). Shows the cost vs working-capital trade-off.
    w0 = with_crn_seed(world, seed + 1000)
    frontier = []
    for trig in range(8, 33, 2):
        for gap in (7, 14, 21, 28, 35):
            e = economics(w0, run_engine(w0, BaselinePolicy(float(trig), float(trig + gap)), t0, world.T,
                                         state=start_state, first_review_day=t0))
            frontier.append(dict(trigger=trig, up_to=trig + gap, total_cost=e["total_cost"],
                                 avg_inventory_value=e["avg_inventory_value"], fill_rate=e["service_level_fill_rate"],
                                 n_orders=e["n_orders"]))
    out["baseline_frontier_hindsight"] = frontier
    if sensitivity:
        sens = {}
        for mult in (0.5, 2.0):  # ORBIT believes stockouts are cheaper/dearer than they truly are
            pol = OrbitPolicy(provider, dataclasses.replace(base_p, stockout_cost_multiplier=mult))
            rows = []
            for rep in range(min(n_rep, 3)):
                w_rep = with_crn_seed(world, seed + 1000 + rep)
                rows.append(economics(w_rep, run_engine(w_rep, pol, t0, world.T, state=start_state,
                                                        first_review_day=t0)))
            sens[str(mult)] = {m: float(np.mean([r[m] for r in rows])) for m in METRICS}
        out["misspecified_stockout_cost"] = dict(
            note="ORBIT run assuming stockout cost x multiplier; all results evaluated under the TRUE cost",
            results=sens)
    return out
