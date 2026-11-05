"""Leakage-safe feature engineering for demand forecasting.

Forecast origin t = last day whose data is known. Target for horizon h = sum of demand over days t+1..t+h.
Every feature is computed from data at days <= t, except calendar facts for the target window (event days,
SNAP days, weekends, month), which are known in advance (they come from the published calendar).
Future *prices* are NOT used because they are not known at the origin.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from orbit.data.supply_sim import World

HORIZONS = (7, 14, 28)
MIN_ORIGIN = 364 + 56  # need one year of history for the seasonal-naive baseline and 56d windows

FEATURE_COLUMNS = [
    "m7", "m14", "m28", "m56", "std28", "zero28", "ratio7_28", "ratio28_56",
    "ly_ratio", "price_now", "price_rel28", "promo_now", "win_event", "win_snap", "win_weekend", "win_sin",
    "win_cos", "cat_foods", "cat_hobbies", "cat_household", "store_0", "store_1", "store_2", "log_scale",
]


def _csum(D: np.ndarray) -> np.ndarray:
    return np.concatenate([np.zeros((D.shape[0], 1)), np.cumsum(D, axis=1)], axis=1)


def build_features(world: World, cal: dict, origins: np.ndarray, horizon: int, with_target: bool = True) -> pd.DataFrame:
    """Return one row per (series, origin). Columns: series, origin, horizon, FEATURE_COLUMNS, y (if with_target),
    seasonal_naive, moving_average (the two baselines), scale (level used to normalise the target)."""
    D, P, S, T = world.D, world.P, world.S, world.T
    cs = _csum(D)
    cs2 = _csum(D ** 2)
    cz = _csum((D == 0).astype(float))
    cp = _csum(P)
    ev_cs = np.concatenate([[0], np.cumsum(cal["event"])])
    wk_cs = np.concatenate([[0], np.cumsum(np.isin(cal["wday"], (1, 2)).astype(int))])  # Sat(1)/Sun(2)
    snap_cs = _csum(cal["snap"].astype(float))
    cat = world.series["cat_id"].to_numpy()
    store_idx = world.store_idx
    rows = []
    for t in origins:
        t = int(t)
        if t < MIN_ORIGIN:
            continue
        if with_target and t + horizon > T - 1:
            continue  # target window not fully observed
        def win_sum(w, end=t):  # sum of days (end-w+1 .. end)
            return cs[:, end + 1] - cs[:, end + 1 - w]
        m7, m14, m28, m56 = win_sum(7) / 7, win_sum(14) / 14, win_sum(28) / 28, win_sum(56) / 56
        var28 = (cs2[:, t + 1] - cs2[:, t - 27]) / 28 - m28 ** 2
        std28 = np.sqrt(np.clip(var28, 0, None))
        zero28 = (cz[:, t + 1] - cz[:, t - 27]) / 28
        # same target window one year (364 days) earlier -- fully inside known history
        a, b = t + 1 - 364, t + horizon - 364
        ly_sum = cs[:, b + 1] - cs[:, a]
        ly_m28 = (cs[:, t + 1 - 364] - cs[:, t + 1 - 364 - 28]) / 28
        ly_ratio = np.where(ly_m28 > 0.05, ly_sum / (horizon * ly_m28 + 1e-9), 1.0)
        ly_ratio = np.clip(ly_ratio, 0.2, 5.0)
        p_now = P[:, t]
        p28 = (cp[:, t + 1] - cp[:, t - 27]) / 28
        pmax28 = np.max(P[:, t - 27 : t + 1], axis=1)
        w0, w1 = t + 1, t + horizon  # target window; calendar arrays are extended past the end of history
        mid = (t + 1 + t + horizon) / 2.0
        doy_mid = pd.Timestamp(world.dates[0]) + pd.Timedelta(days=mid)
        ang = 2 * np.pi * doy_mid.dayofyear / 365.25
        ev_n = ev_cs[w1 + 1] - ev_cs[w0]
        wk_n = wk_cs[w1 + 1] - wk_cs[w0]
        sn_n = snap_cs[:, w1 + 1] - snap_cs[:, w0]
        scale = horizon * np.maximum(m28, 0.2)
        df = pd.DataFrame({
            "series": np.arange(S), "origin": t, "horizon": horizon,
            "m7": m7, "m14": m14, "m28": m28, "m56": m56, "std28": std28, "zero28": zero28,
            "ratio7_28": m7 / np.maximum(m28, 0.05), "ratio28_56": m28 / np.maximum(m56, 0.05),
            "ly_ratio": ly_ratio, "price_now": p_now, "price_rel28": np.clip(p_now / np.maximum(p28, 1e-6), 0, 3),
            "promo_now": (p_now < 0.9 * pmax28).astype(int), "win_event": ev_n, "win_snap": sn_n,
            "win_weekend": wk_n, "win_sin": np.sin(ang), "win_cos": np.cos(ang),
            "cat_foods": (cat == "FOODS").astype(int), "cat_hobbies": (cat == "HOBBIES").astype(int),
            "cat_household": (cat == "HOUSEHOLD").astype(int),
            "store_0": (store_idx == 0).astype(int), "store_1": (store_idx == 1).astype(int),
            "store_2": (store_idx == 2).astype(int), "log_scale": np.log(scale),
            "seasonal_naive": ly_sum, "moving_average": horizon * m28, "scale": scale,
        })
        if with_target:
            df["y"] = cs[:, t + horizon + 1] - cs[:, t + 1]
        rows.append(df)
    out = pd.concat(rows, ignore_index=True)
    return out


def origins_for(world: World, horizon: int, step: int = 7, offset: int = 0) -> np.ndarray:
    """Weekly forecast origins whose full target window lies inside history."""
    return np.arange(MIN_ORIGIN + offset, world.T - horizon, step)
