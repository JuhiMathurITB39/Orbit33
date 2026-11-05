"""Demand forecasting: baselines + sklearn models, rolling-origin backtest, conformal intervals.

Target (per horizon h in {7,14,28}): total units demanded by a product-store over days t+1..t+h, forecast at
origin t using information through day t only.

Validation design
  * Weekly origins. The last `N_FOLDS*FOLD_WEEKS` weeks of origins are split into expanding-window folds.
  * Fold k trains on origins with origin + h <= first test origin of fold k (purge gap = horizon, so no training
    target window overlaps the test period -> no label leakage), then predicts fold k's origins.
  * Folds 0..2 are VALIDATION (model selection, interval calibration). Fold 3 is the untouched TEST fold.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from orbit.data.supply_sim import World
from orbit.ml.features import FEATURE_COLUMNS, build_features, origins_for

N_FOLDS, FOLD_WEEKS = 4, 13
COMPLEXITY_ORDER = ["moving_average", "seasonal_naive", "linear_regression", "random_forest", "hist_gradient_boosting"]
INTERVAL_LEVEL = 0.80
SIMPLICITY_TOLERANCE = 1.02  # choose the simplest model within 2% (relative) of the best validation WAPE


class NormalisedRegressor:
    """Fits base model on y/scale (level-normalised target) and rescales predictions; clips at 0."""

    def __init__(self, base):
        self.base = base

    def fit(self, X: pd.DataFrame, y: np.ndarray, scale: np.ndarray):
        self.base.fit(X[FEATURE_COLUMNS].to_numpy(), y / scale)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return np.clip(self.base.predict(X[FEATURE_COLUMNS].to_numpy()), 0, None) * X["scale"].to_numpy()


def make_model(name: str, seed: int) -> NormalisedRegressor:
    if name == "linear_regression":
        base = make_pipeline(StandardScaler(), LinearRegression())
    elif name == "random_forest":
        base = RandomForestRegressor(n_estimators=120, min_samples_leaf=5, max_features=0.5, n_jobs=2,
                                     random_state=seed)
    elif name == "hist_gradient_boosting":
        base = HistGradientBoostingRegressor(max_iter=250, learning_rate=0.06, max_leaf_nodes=31,
                                             min_samples_leaf=40, l2_regularization=1.0, random_state=seed)
    else:
        raise ValueError(name)
    return NormalisedRegressor(base)


MODEL_PARAMS = {
    "linear_regression": dict(scaler="standard", target="y/scale"),
    "random_forest": dict(n_estimators=120, min_samples_leaf=5, max_features=0.5, target="y/scale"),
    "hist_gradient_boosting": dict(max_iter=250, learning_rate=0.06, max_leaf_nodes=31, min_samples_leaf=40,
                                   l2_regularization=1.0, target="y/scale",
                                   note="histogram gradient boosting; stands in for LightGBM/XGBoost"),
}


def forecast_metrics(y: np.ndarray, pred: np.ndarray) -> dict:
    """MAE/RMSE are in units; WAPE = sum|e|/sum(y) is scale-free and robust to zeros; bias = sum(pred-y)/sum(y)
    (positive = over-forecast); MAPE only over y>0 and is reported with that caveat (it explodes on small y)."""
    y, pred = np.asarray(y, float), np.asarray(pred, float)
    e = pred - y
    pos = y > 0
    return dict(
        mae=float(np.abs(e).mean()), rmse=float(np.sqrt((e ** 2).mean())),
        wape=float(np.abs(e).sum() / max(y.sum(), 1e-9)), bias=float(e.sum() / max(y.sum(), 1e-9)),
        mape_pos=float((np.abs(e[pos]) / y[pos]).mean()) if pos.any() else float("nan"), n=int(len(y)),
    )


def make_folds(origins: np.ndarray, horizon: int) -> list[dict]:
    """Expanding-window rolling-origin folds over the last N_FOLDS*FOLD_WEEKS weekly origins."""
    origins = np.sort(origins)
    block = FOLD_WEEKS
    tail = origins[-N_FOLDS * block:]
    folds = []
    for k in range(N_FOLDS):
        test = tail[k * block:(k + 1) * block]
        train = origins[origins + horizon <= test[0]]  # purge: target window must end before test starts
        folds.append(dict(fold=k, role="validation" if k < N_FOLDS - 1 else "test", train_origins=train,
                          test_origins=test))
    return folds


def _predict_baseline(name: str, df: pd.DataFrame) -> np.ndarray:
    return df[name].to_numpy()


@dataclass
class ForecastResult:
    horizon: int
    metrics: dict            # {model: {"validation": {...}, "test": {...}, "folds": [...]}}
    selected: str
    q_norm: float            # conformal half-width in units of `scale`
    interval_coverage_test: float
    backtest: pd.DataFrame   # rows: series, origin, horizon, model, fold, role, y, pred
    residuals: dict
    final_models: dict       # model name -> fitted on all origins (selected one used for serving)


def run_horizon(world: World, cal: dict, horizon: int, seed: int = 42, models=("linear_regression", "random_forest",
                                                                                   "hist_gradient_boosting")) -> ForecastResult:
    origins = origins_for(world, horizon)
    data = build_features(world, cal, origins, horizon)
    folds = make_folds(origins, horizon)
    per_model_fold = {m: [] for m in list(models) + ["seasonal_naive", "moving_average"]}
    for f in folds:
        tr = data[data.origin.isin(f["train_origins"])]
        te = data[data.origin.isin(f["test_origins"])]
        for b in ("seasonal_naive", "moving_average"):
            p = _predict_baseline(b, te)
            per_model_fold[b].append((f, te, p))
        for m in models:
            mdl = make_model(m, seed).fit(tr, tr["y"].to_numpy(), tr["scale"].to_numpy())
            per_model_fold[m].append((f, te, mdl.predict(te)))
    metrics, all_rows = {}, []
    for m, items in per_model_fold.items():
        val = [(te, p) for f, te, p in items if f["role"] == "validation"]
        test = [(te, p) for f, te, p in items if f["role"] == "test"]
        yv = np.concatenate([te["y"].to_numpy() for te, _ in val]); pv = np.concatenate([p for _, p in val])
        yt = np.concatenate([te["y"].to_numpy() for te, _ in test]); pt = np.concatenate([p for _, p in test])
        metrics[m] = dict(validation=forecast_metrics(yv, pv), test=forecast_metrics(yt, pt),
                          folds=[dict(fold=f["fold"], role=f["role"], **forecast_metrics(te["y"].to_numpy(), p))
                                 for f, te, p in items])
        for f, te, p in items:
            all_rows.append(pd.DataFrame(dict(series=te.series.to_numpy(), origin=te.origin.to_numpy(),
                                              horizon=horizon, model=m, fold=f["fold"], role=f["role"],
                                              y=te["y"].to_numpy(), pred=p, scale=te["scale"].to_numpy())))
    backtest = pd.concat(all_rows, ignore_index=True)
    # model selection on VALIDATION WAPE only: simplest model within tolerance of the best
    best = min(m["validation"]["wape"] for m in metrics.values())
    selected = next(m for m in COMPLEXITY_ORDER if m in metrics and metrics[m]["validation"]["wape"] <= best * SIMPLICITY_TOLERANCE)
    # split-conformal interval calibrated on validation folds of the selected model
    v = backtest[(backtest.model == selected) & (backtest.role == "validation")]
    r = (np.abs(v["y"] - v["pred"]) / v["scale"]).to_numpy()
    n = len(r)
    q_norm = float(np.quantile(r, min(1.0, INTERVAL_LEVEL * (1 + 1 / n))))
    t = backtest[(backtest.model == selected) & (backtest.role == "test")]
    cover = float(((t["y"] >= t["pred"] - q_norm * t["scale"]) & (t["y"] <= t["pred"] + q_norm * t["scale"])).mean())
    # residual analysis on test fold
    t = t.copy()
    t["resid"] = t["pred"] - t["y"]
    t["cat"] = world.series["cat_id"].to_numpy()[t.series.to_numpy()]
    t["store"] = world.series["store_id"].to_numpy()[t.series.to_numpy()]
    ac = []
    for _s, g in backtest[(backtest.model == selected)].sort_values("origin").groupby("series"):
        e = (g["pred"] - g["y"]).to_numpy()
        if len(e) > 5 and e.std() > 0:
            ac.append(float(np.corrcoef(e[:-1], e[1:])[0, 1]))
    hist_edges = np.linspace(-1.5, 1.5, 13)
    nr = ((t["pred"] - t["y"]) / t["scale"]).clip(-1.5, 1.5)
    residuals = dict(
        by_category={k: forecast_metrics(g["y"], g["pred"]) for k, g in t.groupby("cat")},
        by_store={k: forecast_metrics(g["y"], g["pred"]) for k, g in t.groupby("store")},
        mean_lag1_autocorr=float(np.mean(ac)) if ac else None,
        norm_resid_hist=dict(edges=hist_edges.tolist(), counts=np.histogram(nr, hist_edges)[0].tolist()),
    )
    # final models for serving: all origins with fully observed targets
    final = {}
    for m in models:
        final[m] = make_model(m, seed).fit(data, data["y"].to_numpy(), data["scale"].to_numpy())
    return ForecastResult(horizon, metrics, selected, q_norm, cover, backtest, residuals, final)


def serve_forecast(world: World, cal: dict, model, horizon: int, q_norm: float, baseline: str = "moving_average") -> pd.DataFrame:
    """Forecast for the `horizon` days after the last observed day, with conformal interval."""
    origin = world.T - 1
    X = build_features(world, cal, np.array([origin]), horizon, with_target=False)
    if X.empty:
        raise RuntimeError("could not build serving features (insufficient history)")
    pred = model.predict(X) if hasattr(model, "predict") else X[baseline].to_numpy()  # baseline models need no fitted object
    X["point"] = pred
    X["lo"] = np.clip(pred - q_norm * X["scale"], 0, None)
    X["hi"] = pred + q_norm * X["scale"]
    return X[["series", "origin", "horizon", "point", "lo", "hi"]]


def seasonality_analysis(world: World, cal: dict) -> dict:
    """Additive day-of-week and month indices by category, plus weekly seasonal strength of total demand."""
    D, dates = world.D, world.dates
    cat = world.series["cat_id"].to_numpy()
    wday = cal["wday"][: world.T]
    months = dates.month.to_numpy()
    out = {"by_category": {}}
    for c in np.unique(cat):
        tot = D[cat == c].sum(axis=0)
        dow = pd.Series(tot).groupby(wday).mean()
        mon = pd.Series(tot).groupby(months).mean()
        out["by_category"][c] = dict(
            dow_index={int(k): round(float(v / tot.mean()), 4) for k, v in dow.items()},
            month_index={int(k): round(float(v / tot.mean()), 4) for k, v in mon.items()},
        )
    total = D.sum(axis=0)
    trend = pd.Series(total).rolling(7, center=True).mean()
    detr = pd.Series(total) - trend
    seas = detr.groupby(wday).transform("mean")
    rem = detr - seas
    ok = rem.notna()
    out["weekly_seasonal_strength"] = float(max(0.0, 1 - rem[ok].var() / (rem[ok] + seas[ok]).var()))
    acf = lambda x, k: float(np.corrcoef(x[:-k], x[k:])[0, 1])  # noqa: E731
    out["acf_total_demand"] = {"lag_7": acf(total, 7), "lag_364": acf(total, 364)}
    return out
