"""Stockout-risk model.

Target (SIMULATION-DERIVED, see DECISIONS D-008): y = 1 if the product-store has lost sales (demand > on-hand)
on at least one of the next 7 days (t+1..t+7) under the incumbent baseline policy. M5 contains no inventory, so
no observed stockout label exists; this label comes from the simulated inventory ledger.

Features use only information available at the end of day t: inventory position, open orders and their promised
dates, recent demand statistics and recent supplier delivery behaviour.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from orbit.data.supply_sim import World
from orbit.ml.supplier import expected_calibration_error

HORIZON_DAYS = 7
COST_FN, COST_FP = 5.0, 1.0  # ASSUMPTION: a missed stockout costs 5x an unnecessary alert (see MODEL_CARD)
FEATURES = ["on_hand", "on_order", "due7_promised", "overdue_qty", "cover_days", "pos_cover_days", "m7", "m28",
            "std28", "cv28", "zero28", "ratio7_28", "sup_lead_mean", "sup_late_rate", "sup_lead_sd",
            "cover_minus_lead", "price_rel28", "cat_foods", "cat_hobbies", "cat_household"]


def pending_order_arrays(world: World, orders: pd.DataFrame) -> dict:
    """due7_promised[s,t]: qty of orders placed by t, not yet arrived at t, promised to arrive by t+7.
    overdue_qty[s,t]: qty not yet arrived whose promised date has passed. Open orders (no arrival in history) are
    treated as not yet arrived. All quantities are things the planner knows at end of day t."""
    S, T = world.S, world.T
    due7 = np.zeros((S, T)); overdue = np.zeros((S, T))
    for r in orders.itertuples(index=False):
        s, od, q = int(r.series), int(r.order_day), float(r.qty_ordered)
        arr = int(r.arrival_day) if r.observed else T + 100
        promised = od + int(r.promised_lead)
        last = min(arr - 1, T - 1)  # days t where the order is pending: od <= t < arr
        if last < od:
            continue
        t = np.arange(od, last + 1)
        due7[s, t] += np.where(promised <= t + HORIZON_DAYS, q, 0.0)
        overdue[s, t] += np.where(promised < t, q, 0.0)
    return dict(due7=due7, overdue=overdue)


def build_dataset(world: World, inv: dict, pend: dict, stats: dict, origins: np.ndarray) -> pd.DataFrame:
    D, P, S = world.D, world.P, world.S
    cs = np.concatenate([np.zeros((S, 1)), np.cumsum(D, axis=1)], axis=1)
    cs2 = np.concatenate([np.zeros((S, 1)), np.cumsum(D ** 2, axis=1)], axis=1)
    cz = np.concatenate([np.zeros((S, 1)), np.cumsum((D == 0).astype(float), axis=1)], axis=1)
    cl = np.concatenate([np.zeros((S, 1)), np.cumsum((inv["lost"] > 0).astype(float), axis=1)], axis=1)
    cat = world.series["cat_id"].to_numpy()
    rows = []
    for t in origins:
        t = int(t)
        m7 = (cs[:, t + 1] - cs[:, t - 6]) / 7
        m28 = (cs[:, t + 1] - cs[:, t - 27]) / 28
        m56 = (cs[:, t + 1] - cs[:, t - 55]) / 56
        var = (cs2[:, t + 1] - cs2[:, t - 27]) / 28 - m28 ** 2
        std = np.sqrt(np.clip(var, 0, None))
        oh, oo = inv["on_hand"][:, t], inv["on_order"][:, t]
        cover = oh / np.maximum(m28, 0.05)
        pos_cover = (oh + oo) / np.maximum(m28, 0.05)
        sup = world.sup_idx
        lm = np.nan_to_num(stats["lead_mean"][sup, t], nan=0.0)
        lm = np.where(lm > 0, lm, world.sup_mean[sup, t])
        lr = np.nan_to_num(stats["late_rate"][sup, t], nan=0.1)
        p28 = P[:, max(t - 27, 0): t + 1].mean(axis=1)
        df = pd.DataFrame({
            "series": np.arange(S), "origin": t, "on_hand": oh, "on_order": oo, "due7_promised": pend["due7"][:, t],
            "overdue_qty": pend["overdue"][:, t], "cover_days": np.clip(cover, 0, 200),
            "pos_cover_days": np.clip(pos_cover, 0, 200), "m7": m7, "m28": m28, "std28": std,
            "cv28": std / np.maximum(m28, 0.05), "zero28": (cz[:, t + 1] - cz[:, t - 27]) / 28,
            "ratio7_28": m7 / np.maximum(m28, 0.05), "sup_lead_mean": lm, "sup_late_rate": lr,
            "sup_lead_sd": np.nan_to_num(stats["lead_sd"][sup, t], nan=1.0),
            "cover_minus_lead": np.clip(cover, 0, 200) - lm,
            "price_rel28": np.clip(P[:, t] / np.maximum(p28, 1e-6), 0, 3),
            "cat_foods": (cat == "FOODS").astype(int), "cat_hobbies": (cat == "HOBBIES").astype(int),
            "cat_household": (cat == "HOUSEHOLD").astype(int), "selling": (m56 > 0).astype(int),
        })
        if t + HORIZON_DAYS <= world.T - 1:
            df["y"] = ((cl[:, t + HORIZON_DAYS + 1] - cl[:, t + 1]) > 0).astype(int)
        rows.append(df)
    out = pd.concat(rows, ignore_index=True)
    return out[out.selling == 1].reset_index(drop=True)


class Calibrated:
    """Classifier + isotonic calibration fitted on a later validation period."""

    def __init__(self, model, iso: IsotonicRegression):
        self.model, self.iso = model, iso

    def predict_proba1(self, X: np.ndarray) -> np.ndarray:
        return np.clip(self.iso.predict(self.model.predict_proba(X)[:, 1]), 0, 1)


def _metrics(y, p, thr):
    yhat = (p >= thr).astype(int)
    return dict(pr_auc=float(average_precision_score(y, p)), roc_auc=float(roc_auc_score(y, p)),
                precision=float(precision_score(y, yhat, zero_division=0)),
                recall=float(recall_score(y, yhat, zero_division=0)), f1=float(f1_score(y, yhat, zero_division=0)),
                brier=float(brier_score_loss(y, p)), ece=expected_calibration_error(y, p),
                prevalence=float(np.mean(y)), threshold=float(thr), n=int(len(y)),
                false_positives=int(((yhat == 1) & (y == 0)).sum()), false_negatives=int(((yhat == 0) & (y == 1)).sum()))


def best_threshold(y: np.ndarray, p: np.ndarray, c_fn: float = COST_FN, c_fp: float = COST_FP) -> float:
    grid = np.unique(np.quantile(p, np.linspace(0.02, 0.98, 97)))
    costs = [c_fn * ((p < t) & (y == 1)).sum() + c_fp * ((p >= t) & (y == 0)).sum() for t in grid]
    return float(grid[int(np.argmin(costs))])


def calibration_table(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list[dict]:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    return [dict(bin=int(b), mean_pred=float(p[idx == b].mean()), observed=float(y[idx == b].mean()),
                 n=int((idx == b).sum())) for b in range(bins) if (idx == b).any()]


def train_stockout(df: pd.DataFrame, T: int, seed: int = 42) -> dict:
    d = df[df.y.notna()].copy()
    d["y"] = d["y"].astype(int)
    test_start = T - HORIZON_DAYS - 120
    val_start = test_start - 120
    train = d[d.origin <= val_start - HORIZON_DAYS]  # purge: label window (7d) must end before validation begins
    val = d[(d.origin > val_start) & (d.origin <= test_start - HORIZON_DAYS)]
    test = d[d.origin > test_start]
    X = lambda x: x[FEATURES].to_numpy()  # noqa: E731
    cands = {
        "logistic_regression": make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=0.5,
                                                                                  class_weight="balanced")),
        "random_forest": RandomForestClassifier(n_estimators=150, min_samples_leaf=20, max_features="sqrt",
                                                class_weight="balanced_subsample", n_jobs=2, random_state=seed),
        "hist_gradient_boosting": HistGradientBoostingClassifier(max_iter=200, learning_rate=0.06, max_leaf_nodes=15,
                                                                 min_samples_leaf=50, l2_regularization=1.0,
                                                                 class_weight="balanced", random_state=seed),
    }
    res = dict(n=dict(train=len(train), validation=len(val), test=len(test)),
               prevalence=dict(train=float(train.y.mean()), validation=float(val.y.mean()), test=float(test.y.mean())),
               costs=dict(false_negative=COST_FN, false_positive=COST_FP), models={}, calibration={}, fitted={})
    # Reference heuristics (no learning). A planner's rule of thumb is "few days of on-hand cover left".
    # Position cover (on-hand + on-order) is reported too, to show why it is a poor signal on its own: right after
    # an order is placed it is high even though stock is low and the delivery is still in transit.
    for hname, score in (("heuristic_low_onhand_cover", 1 / (1 + test.cover_days.to_numpy())),
                         ("heuristic_low_position_cover", 1 / (1 + test.pos_cover_days.to_numpy()))):
        res["models"][hname] = dict(pr_auc=float(average_precision_score(test.y, score)),
                                    roc_auc=float(roc_auc_score(test.y, score)), prevalence=float(test.y.mean()))
    for name, mdl in cands.items():
        mdl.fit(X(train), train.y.to_numpy())
        raw_val = mdl.predict_proba(X(val))[:, 1]
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(raw_val, val.y.to_numpy())
        cal = Calibrated(mdl, iso)
        p_val, p_test = cal.predict_proba1(X(val)), cal.predict_proba1(X(test))
        thr = best_threshold(val.y.to_numpy(), p_val)
        raw_test = mdl.predict_proba(X(test))[:, 1]
        m = _metrics(test.y.to_numpy(), p_test, thr)
        m["validation_pr_auc"] = float(average_precision_score(val.y, p_val))
        m["brier_uncalibrated"] = float(brier_score_loss(test.y, raw_test))
        m["ece_uncalibrated"] = expected_calibration_error(test.y.to_numpy(), raw_test)
        res["models"][name] = m
        res["calibration"][name] = calibration_table(test.y.to_numpy(), p_test)
        res["fitted"][name] = cal
    res["selected"] = max(cands, key=lambda k: res["models"][k]["validation_pr_auc"])
    return res
