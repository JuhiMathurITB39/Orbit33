# Next Steps

1. Provide the real M5 files and set `ORBIT_M5_DIR`; re-run the pipeline and retrain; replace all simulated-data metrics.
2. In an environment with package-index access: install the `spec_stack` extras; port the SQLite warehouse to PostgreSQL via SQLAlchemy; swap in LightGBM/XGBoost; add MLflow.
3. Run the Airflow DAG and `dbt build` against a real Postgres.
4. Build and validate Docker images; deploy to AWS (ECS + RDS + S3) and record real cost.
5. Run real user interviews with `product/USER_RESEARCH.md` templates.
6. Re-run the backtest with many demand paths (not only supplier randomness) and report intervals; compare against a hold-out period not used for any design decision.
7. Add planner override capture and usage instrumentation (METRICS.md), then a shadow-mode pilot.
8. Type-check the frontend (install `@types/react`), add component tests; run a load test of the API.
9. Add a covering index for the stockout-days-by-product query (345 ms full scan).
