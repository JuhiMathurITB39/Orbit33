"""Leakage control: features at origin t must not change if data after t changes."""
import dataclasses

import numpy as np
import pytest

from orbit.data.world_loader import load_calendar_arrays, load_world
from orbit.ml.features import build_features, origins_for
from orbit.warehouse import db


@pytest.fixture(scope="module")
def world_cal(live_settings):
    c = db.connect(live_settings.db_path, readonly=True)
    w = load_world(c, 42)
    return w, load_calendar_arrays(c, w)


def test_features_invariant_to_future_demand_and_prices(world_cal):
    w, cal = world_cal
    t = np.array([900, 1200])
    X1 = build_features(w, cal, t, 7, with_target=False)
    D2, P2 = w.D.copy(), w.P.copy()
    D2[:, 1201:] = D2[:, 1201:] * 7 + 100   # corrupt everything after the latest origin
    P2[:, 1201:] = 99.0
    X2 = build_features(dataclasses.replace(w, D=D2, P=P2), cal, t, 7, with_target=False)
    cols = [c for c in X1.columns if c not in ("y",)]
    np.testing.assert_allclose(X1[cols].select_dtypes("number").to_numpy(), X2[cols].select_dtypes("number").to_numpy())


def test_target_is_the_future_sum_and_uses_only_after_origin(world_cal):
    w, cal = world_cal
    t = np.array([1000])
    X = build_features(w, cal, t, 14)
    s = int(X.series.iloc[0]) if "series" in X else 0
    row = X[X.series == s].iloc[0]
    assert row["y"] == pytest.approx(w.D[s, 1001:1015].sum())


def test_origins_leave_room_for_target(world_cal):
    w, _ = world_cal
    for h in (7, 14, 28):
        assert origins_for(w, h).max() + h <= w.T - 1


def test_calendar_arrays_cover_serving_window(world_cal):
    w, cal = world_cal
    for k in ("wday", "month", "event", "snap"):
        assert cal[k].shape[-1] >= w.T + 28, k
