"""Run the historical policy backtest and store results.   python -m orbit.optim.run_backtest [--reps 5]"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime

from orbit.config import get_settings
from orbit.data.supply_sim import BaselinePolicy
from orbit.data.world_loader import load_calendar_arrays, load_world
from orbit.logging_setup import setup_logging
from orbit.ml.train import _put_metric
from orbit.optim.simulate import run_backtest
from orbit.pipeline.stages import BASELINE_TRIGGER, BASELINE_UPTO
from orbit.warehouse import db
from orbit.warehouse.serving import SERVING_DDL


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--window-days", type=int, default=365)
    a = ap.parse_args()
    s = get_settings()
    setup_logging(s.log_level)
    conn = db.connect(s.db_path)
    conn.executescript(SERVING_DDL)
    world = load_world(conn, s.seed)
    cal = load_calendar_arrays(conn, world)
    t = time.perf_counter()
    out = run_backtest(world, cal, world.T - a.window_days, BaselinePolicy(BASELINE_TRIGGER, BASELINE_UPTO),
                       n_rep=a.reps, seed=s.seed)
    out["data_kind"] = db.meta_get(conn, "data_kind", "UNKNOWN")
    out["runtime_s"] = round(time.perf_counter() - t, 1)
    out["created_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    _put_metric(conn, "simulation", out)
    s.artifacts_dir.mkdir(parents=True, exist_ok=True)
    (s.artifacts_dir / "simulation.json").write_text(json.dumps(out, indent=2, default=float))
    pol = out["policies"]
    print(f"window {out['window']}  ({out['days']} days)  reps={out['n_replications']}  runtime {out['runtime_s']}s")
    print(f"tuned baseline: trigger={out['tuned_baseline']['trigger']} up_to={out['tuned_baseline']['up_to']}")
    for k, v in pol.items():
        print(f"{k:18s} cost {v['total_cost']['mean']:>9.0f} ±{v['total_cost']['sd']:<6.0f} fill {v['service_level_fill_rate']['mean']:.3f} "
              f"stockout_day {v['stockout_day_rate']['mean']:.3f} avg_inv_value {v['avg_inventory_value']['mean']:>7.0f} "
              f"hold {v['holding_cost']['mean']:>7.0f} order {v['ordering_cost']['mean']:>6.0f} lost {v['lost_sales_cost']['mean']:>7.0f}")
    for k, v in out["paired"].items():
        print(f"{k:42s} rel cost diff {v['mean_rel_diff']*100:+.1f}% ±{v['sd_rel_diff']*100:.1f}  cheaper in {v['share_reps_orbit_cheaper']*100:.0f}% of reps")
    conn.close()


if __name__ == "__main__":
    main()
