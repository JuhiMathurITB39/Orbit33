import sqlite3

import pytest

from orbit.config import Settings
from orbit.pipeline.checkpoint import FaultInjector, InjectedFailure
from orbit.pipeline.run import run_pipeline

TABLES = ("fact_sales", "fact_inventory", "fact_purchase_orders", "fact_replenishment", "dim_product")


def counts(s):
    c = sqlite3.connect(s.db_path)
    out = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}  # noqa: S608
    c.close()
    return out


def test_pipeline_loads_expected_grain(pipeline_dir):
    s, summary = pipeline_dir
    c = counts(s)
    assert c["fact_sales"] == 168 * 1941 == c["fact_inventory"]
    assert c["dim_product"] == 56


def test_no_dq_failures(pipeline_dir):
    s, _ = pipeline_dir
    c = sqlite3.connect(s.db_path)
    assert c.execute("SELECT COUNT(*) FROM dq_results WHERE passed=0").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM dq_results").fetchone()[0] > 10


def test_star_schema_referential_integrity(pipeline_dir):
    s, _ = pipeline_dir
    c = sqlite3.connect(s.db_path)
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    orphan = c.execute("SELECT COUNT(*) FROM fact_sales f LEFT JOIN dim_product p USING(product_key) WHERE p.product_key IS NULL").fetchone()[0]
    assert orphan == 0


def test_no_negative_inventory_and_flow_conservation(pipeline_dir):
    s, _ = pipeline_dir
    c = sqlite3.connect(s.db_path)
    assert c.execute("SELECT COUNT(*) FROM fact_inventory WHERE on_hand_end < 0").fetchone()[0] == 0
    # demand = fulfilled + lost on every row
    bad = c.execute("SELECT COUNT(*) FROM fact_inventory WHERE ABS(units_demanded - units_fulfilled - lost_sales_units) > 1e-6").fetchone()[0]
    assert bad == 0
    # sales fact equals demand in the inventory fact (M5 units are treated as demand)
    diff = c.execute("""SELECT COUNT(*) FROM fact_sales f JOIN fact_inventory i USING(date_key,product_key,store_key)
                        WHERE f.units_sold != i.units_demanded""").fetchone()[0]
    assert diff == 0


def test_scd2_supplier_history_has_no_gaps(pipeline_dir):
    s, _ = pipeline_dir
    c = sqlite3.connect(s.db_path)
    rows = c.execute("SELECT supplier_id, version, valid_from, valid_to FROM dim_supplier ORDER BY supplier_id, version").fetchall()
    by = {}
    for sid, v, a, b in rows:
        by.setdefault(sid, []).append((v, a, b))
    assert len(by["S03"]) == 2 and len(by["S06"]) == 2 and len(by["S01"]) == 1
    for sid, vs in by.items():
        for prev, nxt in zip(vs, vs[1:]):
            assert prev[2] == nxt[1], f"gap/overlap in {sid}"
        assert vs[-1][2] == "9999-12-31"


def test_idempotent_rerun(pipeline_dir):
    s, _ = pipeline_dir
    before = counts(s)
    second = run_pipeline(s)
    assert counts(s) == before
    c = sqlite3.connect(s.db_path)
    done = c.execute("SELECT COUNT(*) FROM etl_checkpoint WHERE status='succeeded'").fetchone()[0]
    assert done > 0
    assert second  # ran to completion


def test_recovery_after_injected_failure_reaches_identical_state(tmp_path):
    s = Settings(data_dir=tmp_path)
    with pytest.raises(InjectedFailure):
        run_pipeline(s, fault=FaultInjector("gold_fact_sales", "2013-06", times=99), retries=1)
    c = sqlite3.connect(s.db_path)
    assert c.execute("SELECT status FROM pipeline_runs ORDER BY started_at DESC LIMIT 1").fetchone()[0] == "failed"
    partial = c.execute("SELECT COUNT(*) FROM fact_sales").fetchone()[0]
    c.close()
    assert partial < 168 * 1941  # genuinely incomplete
    run_pipeline(s)
    ref = counts(s)
    assert ref["fact_sales"] == 168 * 1941


def test_changed_input_is_reprocessed_not_skipped(pipeline_dir, tmp_path):
    """A checkpoint is keyed by input hash: a different hash must not count as done."""
    from orbit.pipeline.checkpoint import Checkpoints
    s, _ = pipeline_dir
    c = sqlite3.connect(s.db_path); c.row_factory = sqlite3.Row
    ck = Checkpoints(c)
    row = c.execute("SELECT stage, batch_key, input_hash FROM etl_checkpoint WHERE status='succeeded' AND input_hash IS NOT NULL LIMIT 1").fetchone()
    assert ck.is_done(row["stage"], row["batch_key"], row["input_hash"])
    assert not ck.is_done(row["stage"], row["batch_key"], "different-hash")
