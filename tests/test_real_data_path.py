"""Regression tests for problems found when the pipeline met real-M5-shaped files (found by a user's first real run)."""
import sqlite3

import numpy as np
import pandas as pd

from orbit.config import Settings
from orbit.data.simulate_m5 import generate_m5_files
from orbit.data.world_loader import load_calendar_arrays, load_world
from orbit.ml.forecast import serve_forecast
from orbit.pipeline.run import run_pipeline
from orbit.warehouse import db


def test_calendar_longer_than_sales_is_truncated_and_subset_applies(tmp_path, monkeypatch):
    """Real M5: calendar has 1,969 rows but sales only d_1..d_1941; days without sales must not enter the warehouse."""
    m5 = tmp_path / "m5"
    generate_m5_files(m5, seed=2, stores=("CA_1", "CA_2"), items_per_dept=3)
    cal = pd.read_csv(m5 / "calendar.csv")
    ext = cal.tail(28).copy()
    ext["d"] = [f"d_{len(cal) + i + 1}" for i in range(28)]
    ext["date"] = (pd.to_datetime(cal["date"].iloc[-1]) + pd.to_timedelta(np.arange(1, 29), unit="D")).strftime("%Y-%m-%d")
    pd.concat([cal, ext]).to_csv(m5 / "calendar.csv", index=False)
    monkeypatch.setenv("ORBIT_M5_DIR", str(m5))
    monkeypatch.setenv("ORBIT_ITEMS_PER_DEPT", "2")
    s = Settings(data_dir=tmp_path / "data")
    run_pipeline(s)
    c = sqlite3.connect(s.db_path)
    n_days = c.execute("SELECT COUNT(*) FROM dim_date").fetchone()[0]
    assert n_days == len(cal) == 1941  # the 28 extra calendar rows were dropped
    assert c.execute("SELECT COUNT(*) FROM dim_product").fetchone()[0] == 14      # 2 items x 7 departments
    assert c.execute("SELECT COUNT(*) FROM fact_inventory").fetchone()[0] == 14 * 2 * n_days
    assert c.execute("SELECT value FROM meta_kv WHERE key='data_kind'").fetchone()[0] == "REAL_M5"
    assert c.execute("SELECT COUNT(*) FROM dq_results WHERE passed=0").fetchone()[0] == 0
    # unknown store ids (CA_2) get a synthetic DC rather than crashing
    assert c.execute("SELECT COUNT(*) FROM dim_store WHERE store_id='CA_2'").fetchone()[0] == 1


def test_baseline_winner_is_still_served(live_settings):
    """If a baseline (moving average) wins model selection, forecasts must still be produced for the API."""
    conn = db.connect(live_settings.db_path, readonly=True)
    w = load_world(conn, 42)
    cal = load_calendar_arrays(conn, w)
    out = serve_forecast(w, cal, None, 14, q_norm=0.5, baseline="moving_average")
    assert len(out) == w.S and (out["point"] >= 0).all() and (out["lo"] <= out["point"]).all() and (out["point"] <= out["hi"]).all()


def test_position_table_requires_all_served_horizons(live_settings):
    c = sqlite3.connect(live_settings.db_path)
    got = {r[0] for r in c.execute("SELECT DISTINCT horizon FROM serving_forecast")}
    assert got == {7, 14, 28}
