"""Supplier delivery-risk model (SIMULATION: supplier behaviour comes from synthetic parameters, see supply_sim).

Task: at the moment a purchase order is placed, predict whether it will arrive late (actual lead - promised lead
>= LATE_THRESHOLD_DAYS). Features use only deliveries that had ALREADY ARRIVED before the order date (a delivery's
lateness is unknown until it arrives). Splits are by order date and purge training orders whose arrival falls
inside the later period, so no label from the future is used for training.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from orbit.data.supply_sim import LATE_THRESHOLD_DAYS

WINDOW = 40
SUPPLIER_FEATURES = ["late_rate_recent", "lead_mean_recent", "lead_sd_recent", "n_obs_recent", "promised_lead",
                     "lead_vs_promised", "qty_over_cap", "log_qty", "cat_foods", "cat_hobbies", "cat_household",
                     "sin_m", "cos_m"]


def rolling_supplier_stats(orders: pd.DataFrame, n_sup: int, T: int, window: int = WINDOW) -> dict:
    """For every supplier and day t: stats over the last `window` deliveries that arrived on or before t.
    `orders` needs columns: sup (index), arrival_day (observed arrivals only), actual_lead, is_late.
    Returns arrays (n_sup, T): late_rate, lead_mean, lead_sd, n_obs (n_obs = deliveries in window)."""
    late = np.zeros((n_sup, T)); mean = np.zeros((n_sup, T)); sd = np.zeros((n_sup, T)); n = np.zeros((n_sup, T))
    days = np.arange(T)
    for i in range(n_sup):
        g = orders[orders.sup == i].sort_values("arrival_day")
        if g.empty:
            continue
        a = g.arrival_day.to_numpy(); L = g.actual_lead.to_numpy(float); Lt = g.is_late.to_numpy(float)
        c1 = np.concatenate([[0], np.cumsum(L)]); c2 = np.concatenate([[0], np.cumsum(L ** 2)])
        cl = np.concatenate([[0], np.cumsum(Lt)])
        hi = np.searchsorted(a, days, side="right")  # arrivals with arrival_day <= t
        lo = np.maximum(0, hi - window)
        cnt = hi - lo
        with np.errstate(invalid="ignore", divide="ignore"):
            m = np.where(cnt > 0, (c1[hi] - c1[lo]) / cnt, np.nan)
            v = np.where(cnt > 1, ((c2[hi] - c2[lo]) / cnt - m ** 2) * cnt / np.maximum(cnt - 1, 1), 0.0)
            lr = np.where(cnt > 0, (cl[hi] - cl[lo]) / cnt, np.nan)
        late[i], mean[i], sd[i], n[i] = lr, m, np.sqrt(np.clip(v, 0, None)), cnt
    return dict(late_rate=late, lead_mean=mean, lead_sd=sd, n_obs=n)


def build_po_dataset(orders: pd.DataFrame, world, stats: dict) -> pd.DataFrame:
    """One row per PO placed. `orders`: series, order_day, qty_ordered, promised_lead, arrival_day, actual_lead,
    delay_days, observed (bool: arrived within history). Stats are looked up at order_day - 1 (strictly earlier
    information) so a delivery arriving the same day as the order is not used."""
    o = orders.copy()
    o["sup"] = world.sup_idx[o.series.to_numpy()]
    t_prev = np.maximum(o.order_day.to_numpy() - 1, 0)
    o["late_rate_recent"] = stats["late_rate"][o.sup, t_prev]
    o["lead_mean_recent"] = stats["lead_mean"][o.sup, t_prev]
    o["lead_sd_recent"] = stats["lead_sd"][o.sup, t_prev]
    o["n_obs_recent"] = stats["n_obs"][o.sup, t_prev]
    contract = world.sup_mean[o.sup, o.order_day]
    o["lead_mean_recent"] = o["lead_mean_recent"].fillna(pd.Series(contract, index=o.index))
    o["late_rate_recent"] = o["late_rate_recent"].fillna(
        pd.Series(1 - world.sup_ontime[o.sup, o.order_day], index=o.index))
    o["lead_vs_promised"] = o["lead_mean_recent"] - o["promised_lead"]
    cap = world.sup_capacity[o.sup, o.order_day]
    o["qty_over_cap"] = o.qty_ordered / cap
    o["log_qty"] = np.log1p(o.qty_ordered)
    cat = world.series["cat_id"].to_numpy()[o.series.to_numpy()]
    for c in ("FOODS", "HOBBIES", "HOUSEHOLD"):
        o[f"cat_{c.lower()}"] = (cat == c).astype(int)
    month = world.dates[o.order_day.to_numpy()].month.to_numpy()
    o["sin_m"], o["cos_m"] = np.sin(2 * np.pi * month / 12), np.cos(2 * np.pi * month / 12)
    o["is_late"] = (o.delay_days >= LATE_THRESHOLD_DAYS).astype(int)
    return o


def expected_calibration_error(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    ece = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            ece += m.mean() * abs(y[m].mean() - p[m].mean())
    return float(ece)


def train_supplier_risk(po: pd.DataFrame, T: int, seed: int = 42) -> dict:
    d = po[po.observed].copy()
    test_start, val_start = T - 365, T - 365 - 365
    # purge: a training order is only usable if its outcome was known before the later period began
    train = d[(d.order_day < val_start) & (d.arrival_day < val_start)]
    val = d[(d.order_day >= val_start) & (d.order_day < test_start) & (d.arrival_day < test_start)]
    test = d[d.order_day >= test_start]
    X = lambda df: df[SUPPLIER_FEATURES].to_numpy()  # noqa: E731
    cands = {
        "logistic_regression": make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, C=1.0)),
        "hist_gradient_boosting": HistGradientBoostingClassifier(max_iter=150, learning_rate=0.06,
                                                                 max_leaf_nodes=15, min_samples_leaf=40,
                                                                 random_state=seed),
    }
    out = {"n": dict(train=len(train), validation=len(val), test=len(test)),
           "prevalence_test": float(test.is_late.mean()), "models": {}, "fitted": {}}
    # baseline: the supplier's recent late rate (no model)
    out["models"]["baseline_recent_late_rate"] = _bin_metrics(test.is_late.to_numpy(), test.late_rate_recent.to_numpy())
    for name, mdl in cands.items():
        mdl.fit(X(train), train.is_late.to_numpy())
        out["models"][name] = _bin_metrics(test.is_late.to_numpy(), mdl.predict_proba(X(test))[:, 1])
        out["models"][name]["validation_pr_auc"] = float(average_precision_score(val.is_late, mdl.predict_proba(X(val))[:, 1]))
        out["fitted"][name] = mdl
    best = max(cands, key=lambda k: out["models"][k]["validation_pr_auc"])
    out["selected"] = best
    return out


def _bin_metrics(y: np.ndarray, p: np.ndarray) -> dict:
    return dict(pr_auc=float(average_precision_score(y, p)), roc_auc=float(roc_auc_score(y, p)),
                brier=float(brier_score_loss(y, np.clip(p, 0, 1))), ece=expected_calibration_error(y, np.clip(p, 0, 1)),
                prevalence=float(y.mean()), n=int(len(y)))
