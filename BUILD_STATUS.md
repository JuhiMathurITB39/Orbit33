# ORBIT — Build Status

_Last updated: 2026-11-05 (Phase 1 complete)_

## Phase 1: Environment inspection (VERIFIED by running commands)

### Blocked by session network policy (HTTP 403 on CONNECT, per proxy status endpoint)
| Host | Needed for | Evidence |
|---|---|---|
| www.kaggle.com | M5 Forecasting dataset (the primary real dataset) | `recentRelayFailures`: connect_rejected 403 |
| pypi.org | pip installs (FastAPI, SQLAlchemy, XGBoost, LightGBM, statsmodels, MLflow, dbt, Airflow, PyArrow, Ruff, mypy) | connect_rejected 403; direct DNS also fails |
| registry.npmjs.org | npm installs (Vite, Recharts, Tailwind plugins, etc.) | `npm view` network error |

No attempt has been made to bypass these restrictions, and none will be.

### Available locally
- Python 3.13: pandas 3.0, numpy 2.5, scipy 1.18, scikit-learn 1.9, pydantic 2.13, pytest 9.1, uvicorn 0.54, starlette (importable), matplotlib, httpx2, requests
- PostgreSQL 16 server binaries (`initdb`, `postgres`, `psql`, `pg_dump`), usable without Docker
- Node 22; global React 19, react-dom, TypeScript, esbuild, tailwindcss, playwright, mermaid-cli (in /opt/npm-tools)
- Docker CLI only; **no Docker daemon** (cannot build/run containers or validate Compose here)
- 2 CPUs, 7.8 GB RAM

### NOT available locally
FastAPI, SQLAlchemy, psycopg, PyArrow/Parquet, XGBoost, LightGBM, statsmodels, MLflow, dbt, Airflow, Ruff, mypy, Vite, Recharts, AWS access, Docker daemon, the M5 files.

## Implications for the directive (honest scope)
- **Cannot be done as specified:** use of real M5 data; XGBoost/LightGBM models; Parquet; MLflow; dbt run; Airflow execution; Docker validation; AWS deployment.
- **Can be done with local substitutes (must be labelled as substitutes):** Starlette/pydantic API instead of FastAPI; psycopg-free Postgres access via `psql`/stdlib is awkward, so SQLite-or-Postgres decision pending; esbuild-bundled React + hand-built SVG charts instead of Vite + Recharts; sklearn GradientBoosting/HistGradientBoosting instead of XGBoost/LightGBM.
- **Any results produced on synthetic demand data are NOT M5 results** and will never be presented as such.

## Status (final, 2026-11-05)
| Component | State | Evidence |
|---|---|---|
| Data simulator (M5 schema) + supply simulator | VERIFIED (simulated) | tests/test_reliability.py |
| Medallion pipeline, checkpoints, DQ, quarantine | VERIFIED | tests/test_pipeline.py, test_quality.py |
| Star schema (SQLite) | VERIFIED | tests |
| PostgreSQL 16 DDL + full data load | VERIFIED locally | TEST_RESULTS.md |
| dbt models | SQL verified on PG16; dbt NOT run | DECISIONS D-017 |
| Airflow DAG | NOT EXECUTED (syntax-checked) | |
| Forecast / stockout / supplier models, registry | VERIFIED (simulated data) | MODEL_CARD.md |
| Optimiser + historical backtest | VERIFIED (simulated) | tests/test_optimiser.py, simulation.json |
| API (Starlette) + assistant | VERIFIED | tests/test_api.py |
| Frontend, 7 screens | VERIFIED by headless browser; NOT type-checked | scripts/e2e_ui.js |
| Docs, product artefacts, interview guides | WRITTEN | |
| Dockerfile / Compose / Terraform | AUTHORED, NOT EXECUTED | |
| Cloud deployment | NOT DONE | |
| Real M5 data | NOT AVAILABLE | |
| User research / A/B test | NOT DONE (design only) | |
