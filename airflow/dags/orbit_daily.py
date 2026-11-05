"""ORBIT daily DAG (Airflow 2.x). AUTHORED BUT NOT EXECUTED in the build environment (Airflow could not be installed);
only syntax-checked. Tasks are thin wrappers over the same idempotent CLI entry points the tests exercise, so a retry
or backfill of any run date is safe: checkpoints are keyed by input hash and gold loads are delete-then-insert."""
from __future__ import annotations

from datetime import datetime, timedelta

from airflow.operators.bash import BashOperator

from airflow import DAG

default_args = {"owner": "orbit", "retries": 2, "retry_delay": timedelta(minutes=5), "retry_exponential_backoff": True,
                "execution_timeout": timedelta(hours=1)}

with DAG("orbit_daily", start_date=datetime(2026, 1, 1), schedule="0 3 * * *", catchup=False, max_active_runs=1,
         default_args=default_args, tags=["orbit"], doc_md=__doc__) as dag:
    ingest = BashOperator(task_id="bronze_silver_gold_pipeline", bash_command="python -m orbit.pipeline.run")
    dq_gate = BashOperator(task_id="data_quality_gate", bash_command=(
        "python - <<'PY'\nimport sqlite3,sys\nc=sqlite3.connect('data/orbit.db')\n"
        "n=c.execute('select count(*) from dq_results where passed=0 and run_id=(select run_id from pipeline_runs order by started_at desc limit 1)').fetchone()[0]\n"
        "sys.exit(1 if n else 0)\nPY"))
    train = BashOperator(task_id="train_models", bash_command="python -m orbit.ml.train")
    backtest = BashOperator(task_id="policy_backtest", bash_command="python -m orbit.optim.run_backtest --reps 5")
    dbt_run = BashOperator(task_id="dbt_build", bash_command="cd dbt_project && dbt build --target prod")
    ingest >> dq_gate >> train >> [backtest, dbt_run]
