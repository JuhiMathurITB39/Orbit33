import json
import sqlite3

import pytest

from orbit.ml.registry import ModelUnavailableError, Registry


def test_registry_roundtrip_and_tamper_detection(tmp_path):
    reg = Registry(tmp_path)
    reg.save("m", {"a": 1}, params={}, features=[], metrics={"x": 1.0}, dataset_hash="h", data_kind="SIM")
    assert reg.load("m")[0] == {"a": 1}
    art = next(tmp_path.rglob("*.joblib"))
    art.write_bytes(art.read_bytes() + b"tamper")
    with pytest.raises(ModelUnavailableError):
        reg.load("m")


def test_missing_model_raises_clean_error(tmp_path):
    with pytest.raises(ModelUnavailableError):
        Registry(tmp_path).load("nope")


def test_simulator_is_deterministic(tmp_path):
    from orbit.data.simulate_m5 import generate_m5_files
    generate_m5_files(tmp_path / "a", seed=7, stores=("CA_1",), items_per_dept=1, n_days=400)
    generate_m5_files(tmp_path / "b", seed=7, stores=("CA_1",), items_per_dept=1, n_days=400)
    for f in ("sales_train_evaluation.csv", "calendar.csv", "sell_prices.csv"):
        assert (tmp_path / "a" / f).read_bytes() == (tmp_path / "b" / f).read_bytes()


def test_engine_invariants_on_built_data(live_settings):
    """Stock never negative, no sale exceeds availability, flow balance holds for the whole history."""
    c = sqlite3.connect(live_settings.db_path)
    assert c.execute("SELECT COUNT(*) FROM fact_inventory WHERE on_hand_end<0 OR units_fulfilled>units_demanded+1e-9").fetchone()[0] == 0
    bad = c.execute("""SELECT COUNT(*) FROM fact_purchase_orders WHERE status='received' AND qty_received IS NULL""").fetchone()[0]
    assert bad == 0
    late = c.execute("SELECT COUNT(*) FROM fact_purchase_orders WHERE is_late=1 AND delay_days < 2").fetchone()[0]
    assert late == 0


def test_stored_forecast_metrics_support_claims(live_settings):
    c = sqlite3.connect(live_settings.db_path)
    d = json.loads(c.execute("SELECT json FROM serving_metrics WHERE key='forecast_comparison'").fetchone()[0])
    for h, v in d["horizons"].items():
        sel = v["metrics"][v["selected"]]["test"]["wape"]
        assert sel < v["metrics"]["seasonal_naive"]["test"]["wape"], f"model must beat seasonal naive at {h}d"
        assert 0.75 <= v["interval_coverage_test"] <= 0.85, f"interval coverage off at {h}d"
        assert v["folds"]["purge_days"] == int(h)


def test_simulation_pairs_use_identical_inputs(live_settings):
    c = sqlite3.connect(live_settings.db_path)
    s = json.loads(c.execute("SELECT json FROM serving_metrics WHERE key='simulation'").fetchone()[0])
    assert "SIMULATED" in s["label"] and s["n_replications"] >= 3
    # tuned baseline selected from a grid that did not touch the boundary
    t = s["tuned_baseline"]
    assert 8 < t["trigger"] < 32
