"""Pipeline orchestrator + CLI.   python -m orbit.pipeline.run [--data-dir DIR] [--seed N] [--reset]

Stage graph (each arrow is a real data dependency, mirrored by airflow/dags/orbit_pipeline.py):

  discover -> bronze_calendar_prices -> bronze_sales -> silver_calendar_prices -> silver_sales
           -> synth_supply -> gold_dims -> gold_sales -> gold_supply -> gold_quality
"""

from __future__ import annotations

import argparse
import json

try:
    import resource  # Unix only
except ImportError:  # Windows
    resource = None
import shutil
import statistics
import time
import uuid
from dataclasses import replace
from pathlib import Path

from orbit.config import Settings, get_settings
from orbit.logging_setup import get_logger, setup_logging
from orbit.pipeline import stages as st
from orbit.pipeline.checkpoint import Checkpoints, FaultInjector, now_iso
from orbit.warehouse import db

log = get_logger("orbit.pipeline")

STAGE_ORDER = ["discover", "bronze_calendar_prices", "bronze_sales", "silver_calendar_prices", "silver_sales",
               "synth_supply", "gold_dims", "gold_sales", "gold_supply", "gold_quality"]

BENCH_QUERIES = {
    "daily_sales_by_store_month": (
        "SELECT s.store_id, d.year, d.month, SUM(f.units_sold) FROM fact_sales f "
        "JOIN dim_store s USING(store_key) JOIN dim_date d USING(date_key) GROUP BY 1,2,3"),
    "single_series_history": (
        "SELECT d.date, f.units_sold FROM fact_sales f JOIN dim_date d USING(date_key) "
        "WHERE f.product_key = 1 AND f.store_key = 1 ORDER BY f.date_key"),
    "stockout_days_by_product": (
        "SELECT p.item_id, SUM(i.stockout_flag), COUNT(*) FROM fact_inventory i JOIN dim_product p USING(product_key) "
        "GROUP BY 1"),
    "supplier_late_rate": (
        "SELECT su.supplier_id, AVG(po.is_late), COUNT(*) FROM fact_purchase_orders po "
        "JOIN dim_supplier su USING(supplier_key) WHERE po.status != 'open' GROUP BY 1"),
}


def run_pipeline(settings: Settings | None = None, fault: FaultInjector | None = None, retries: int = 2,
                 reset: bool = False) -> dict:
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    if reset and settings.data_dir.exists():
        for sub in ("bronze", "silver", "gold", "orbit.db", "orbit.db-wal", "orbit.db-shm"):
            p = settings.data_dir / sub
            shutil.rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = db.connect(settings.db_path)
    db.init_schema(conn)
    run_id = uuid.uuid4().hex[:12]
    ctx = st.Ctx(settings=settings, conn=conn, ck=Checkpoints(conn), run_id=run_id, fault=fault or FaultInjector(),
                 retries=retries)
    conn.execute("INSERT INTO pipeline_runs(run_id,started_at,status) VALUES (?,?,?)", (run_id, now_iso(), "running"))
    conn.commit()
    t0 = time.perf_counter()
    try:
        st.discover_source(ctx)
        st.bronze_calendar_prices(ctx)
        st.bronze_sales(ctx)
        cal = st.silver_calendar_prices(ctx)
        st.silver_sales(ctx, cal)
        st.supply_stage(ctx, cal)
        st.gold_dims(ctx, cal)
        st.gold_sales(ctx)
        st.gold_supply(ctx)
        st.gold_quality(ctx)
    except BaseException as exc:
        conn.execute("UPDATE pipeline_runs SET finished_at=?, status='failed', metrics_json=? WHERE run_id=?",
                     (now_iso(), json.dumps({"error": f"{type(exc).__name__}: {exc}"[:300]}), run_id))
        conn.commit()
        conn.close()
        raise
    total = time.perf_counter() - t0
    summary = collect_metrics(ctx, conn, total)
    conn.execute("UPDATE pipeline_runs SET finished_at=?, status='succeeded', data_kind=?, metrics_json=? WHERE run_id=?",
                 (now_iso(), ctx.data_kind, json.dumps(summary), run_id))
    conn.commit()
    conn.close()
    return summary


def _peak_rss_mb():
    """Peak resident memory in MB; None where the `resource` module does not exist (Windows)."""
    if resource is None:
        return None
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def _dir_bytes(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def collect_metrics(ctx: st.Ctx, conn, total_s: float) -> dict:
    s = ctx.settings
    raw_bytes = sum((ctx.source_dir / f).stat().st_size for f in st.M5_FILES)
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    rows = {t: q(f"SELECT COUNT(*) FROM {t}") for t in  # noqa: S608
            ("fact_sales", "fact_inventory", "fact_purchase_orders", "fact_replenishment", "dim_product",
             "dim_store", "dim_date", "dim_supplier", "dim_warehouse")}
    qt = {}
    for name, sql in BENCH_QUERIES.items():
        times = []
        for _ in range(7):
            t = time.perf_counter(); conn.execute(sql).fetchall(); times.append((time.perf_counter() - t) * 1000)
        plan = [r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql).fetchall()]
        qt[name] = dict(median_ms=round(statistics.median(times), 2), min_ms=round(min(times), 2), plan=plan)
    dq_rows = conn.execute("SELECT check_name, SUM(passed=0) f, SUM(n_failed) nf FROM dq_results WHERE run_id=? "
                           "GROUP BY check_name", (ctx.run_id,)).fetchall()
    stage_rows_out = sum(m["rows_out"] for m in ctx.metrics.values())
    summary = dict(
        run_id=ctx.run_id, data_kind=ctx.data_kind, total_seconds=round(total_s, 3),
        peak_rss_mb=_peak_rss_mb(),
        raw_source_bytes=raw_bytes,
        layer_bytes=dict(bronze=_dir_bytes(s.bronze_dir), silver=_dir_bytes(s.silver_dir),
                         warehouse_db=s.db_path.stat().st_size),
        warehouse_rows=rows, stage_metrics={k: {kk: (round(vv, 3) if isinstance(vv, float) else vv)
                                                for kk, vv in v.items()} for k, v in ctx.metrics.items()},
        throughput_rows_per_s=round(stage_rows_out / total_s, 1) if total_s else None,
        total_rows_written_all_stages=stage_rows_out,
        dq=dict(checks_failed_this_run=int(sum(r["f"] or 0 for r in dq_rows)),
                failed_records_this_run=int(sum(r["nf"] or 0 for r in dq_rows if r["f"]))),
        queries=qt,
    )
    s.artifacts_dir.mkdir(parents=True, exist_ok=True)
    (s.artifacts_dir / "pipeline_metrics.json").write_text(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the ORBIT data pipeline")
    ap.add_argument("--data-dir", type=Path)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--reset", action="store_true", help="delete bronze/silver/gold + warehouse first")
    ap.add_argument("--fail-stage", help="fault injection (testing): fail this stage once")
    ap.add_argument("--fail-batch")
    ap.add_argument("--retries", type=int, default=2)
    a = ap.parse_args()
    s = get_settings()
    if a.data_dir:
        s = replace(s, data_dir=a.data_dir)
    if a.seed is not None:
        s = replace(s, seed=a.seed)
    fault = FaultInjector(a.fail_stage, a.fail_batch, times=1) if a.fail_stage else None
    out = run_pipeline(s, fault=fault, retries=a.retries, reset=a.reset)
    print(json.dumps({k: out[k] for k in ("run_id", "data_kind", "total_seconds", "peak_rss_mb",
                                           "warehouse_rows", "throughput_rows_per_s", "dq")}, indent=2))


if __name__ == "__main__":
    main()
