# ORBIT — AI-powered retail supply-chain intelligence

ORBIT helps a replenishment planner see **what will sell, what is about to run out, what to order, and what happens if conditions change** — from a medallion data pipeline through forecasting and a constrained inventory optimiser to an API, a 7-screen UI and a grounded assistant.

> **Read this first — what is real and what is not**
> - **Demand data is simulated.** The build environment could not download M5 (Kaggle blocked). The pipeline reads the real M5 file schema and `ORBIT_M5_DIR` swaps in real files, but every number here comes from a seeded simulator. **None of these are M5 results.**
> - Inventory, suppliers, purchase orders and costs are synthetic by design (M5 has none).
> - Nothing is deployed; there is no live demo. Airflow, dbt, Docker and Terraform files were authored but not run under their own runtimes (see TEST_RESULTS.md).
> - Several stack substitutions were forced (Starlette for FastAPI, SQLite + PostgreSQL DDL, sklearn for LightGBM, esbuild + SVG for Vite + Recharts). See DECISIONS.md.

## Headline findings (simulated data)
| Area | Result |
|---|---|
| Pipeline | 12.4 s, 326k fact rows, 0 DQ failures; idempotent re-run; verified recovery after injected failure |
| Forecast (7 d, test fold) | WAPE 20.5% (random forest) vs 31.5% seasonal naive vs 23.1% moving average; 80% interval covered 81% |
| Stockout model | PR-AUC 0.81 vs 0.70 for a fair on-hand-cover baseline (labels are simulation-derived, so likely optimistic) |
| Supplier-late model | weak: PR-AUC 0.31 vs 0.29 baseline |
| Policy backtest (365 days, one demand path) | ORBIT $42.9k vs tuned incumbent $45.6k (−5.8%) vs default incumbent $127.8k (−66%); **3.7× the default's inventory value**; replacing the ML forecast with a moving average changes cost by only 0.6% |
**Real-data caution:** a first run on real M5 (low-volume items) did not reproduce the optimiser's advantage over a tuned incumbent - see KNOWN_LIMITATIONS #26. Treat the simulated headline as conditional on the simulator's volumes and storage cap.

The honest takeaway: the decision layer (batching, constraints, cover levels) mattered far more than forecast accuracy. The optimiser was redesigned after seeing the first backtest (DECISIONS D-012).

## Run it
```bash
pip install -e .                      # numpy pandas scipy scikit-learn pydantic starlette uvicorn joblib
python -m orbit.pipeline.run --reset  # bronze → silver → gold
python -m orbit.ml.train              # models + serving tables
python -m orbit.optim.run_backtest    # policy backtest
(cd frontend && npm install && npm run build)
python -m orbit.api.app               # http://localhost:8000
python -m pytest tests                # 71 tests
```
Real M5 (full file has ~30k series; start with a subset): `export ORBIT_M5_DIR=/path/to/m5 ORBIT_STORES=CA_1,TX_1,WI_1 ORBIT_ITEMS_PER_DEPT=8` (PowerShell: `$env:ORBIT_M5_DIR="..."`) (calendar.csv, sell_prices.csv, sales_train_evaluation.csv) and re-run everything; re-generate every number before quoting it.

## Where things are
`orbit/` pipeline, warehouse, ML, optimiser, API, assistant · `frontend/` React + TS · `sql/postgres/` DDL · `dbt_project/` · `airflow/dags/` · `infra/` Terraform · `product/` PRD, personas, RICE, metrics, experiment plan, roadmap · `interview/` role guides · docs: ARCHITECTURE, DATA_DICTIONARY, MODEL_CARD, API_DOCUMENTATION, BENCHMARKS, SECURITY, CONTRIBUTING, INTERVIEW_GUIDE · logs: BUILD_STATUS, DECISIONS, TEST_RESULTS, KNOWN_LIMITATIONS, NEXT_STEPS · screenshots in `docs/screenshots/`.
