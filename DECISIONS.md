# Engineering Decisions Log

Each entry: decision, reason, consequence. Substitutions forced by the build environment are marked **[SUBSTITUTION]**.

## D-001 Build environment constraints (2026-11-05)
The sandbox blocks pypi.org, registry.npmjs.org and www.kaggle.com (HTTP 403 at the egress proxy), has no Docker daemon, and has no AWS access. Verified in `BUILD_STATUS.md`. No bypass was attempted.

## D-002 Dataset: M5-schema simulator now, real M5 later **[SUBSTITUTION]**
- The real M5 files could not be downloaded. The pipeline reads the **real M5 file schema** (`calendar.csv`, `sell_prices.csv`, `sales_train_evaluation.csv`, wide format `d_1..d_N`).
- `orbit.data.simulate_m5` generates files with the identical schema and the same calendar (2011-01-29 -> 2016-05-22, 1,941 days = d_1..d_1941) from a seeded stochastic process (weekly + yearly seasonality, trend, event and SNAP effects, price elasticity, intermittent negative-binomial demand).
- If `ORBIT_M5_DIR` points at real M5 files, the pipeline ingests those instead; no code change.
- **Every metric produced from simulated demand is a result on simulated data and is labelled so everywhere (API, UI, docs). It is not an M5 result.**
- Real-M5-specific licence/size claims are *not* made; they were not verified.

## D-003 Storage: SQLite warehouse + gzip-CSV lake; Postgres DDL validated separately **[SUBSTITUTION]**
- No PyArrow -> no Parquet. Bronze/silver/gold layers are written as month-partitioned `.csv.gz` with a JSON manifest.
- No psycopg/SQLAlchemy -> runtime warehouse is SQLite (stdlib). A Postgres 16 DDL for the same star schema lives in `sql/postgres/` and is validated by loading it into a local PostgreSQL 16 instance (see TEST_RESULTS.md for whether that ran).
- Consequence: Postgres-specific features (declarative partitioning, concurrent indexes) are documented but not exercised by the application.

## D-004 API: Starlette + Pydantic instead of FastAPI **[SUBSTITUTION]**
FastAPI is not installable. Starlette (FastAPI's foundation) and Pydantic v2 are present. Routing, validation, error handling and OpenAPI-style docs are hand-built (`/api/v1/openapi.json` is generated from the Pydantic models).

## D-005 ML: scikit-learn models in place of XGBoost/LightGBM/statsmodels/MLflow **[SUBSTITUTION]**
- `HistGradientBoostingRegressor/Classifier` stands in for LightGBM/XGBoost (same algorithm family: histogram-based gradient boosting). It is **not** LightGBM or XGBoost and is named honestly as `HistGradientBoosting`.
- Experiment tracking: a small file-based registry (`orbit.ml.registry`) records run id, params, features, dataset hash, metrics, training time, and artefact SHA-256 (MLflow-equivalent fields, no MLflow).
- Seasonality analysis uses numpy/scipy (STL/statsmodels unavailable): classical additive decomposition by day-of-week and week-of-year means.

## D-006 Frontend: React 19 + TypeScript bundled with esbuild, hand-built SVG charts, hand-written CSS **[SUBSTITUTION]**
Vite and Recharts are not installable (Tailwind exists locally but was not used). React 19, esbuild and TypeScript exist locally. esbuild bundles the TSX (it strips types without checking them). `tsc --noEmit` cannot resolve `react` types (no `@types/react`), so **the frontend is not type-checked here**; the bundle builds and all 7 screens were exercised in headless Chromium with zero console errors. Styling is a small hand-written CSS file (light/dark via `prefers-color-scheme`).

## D-007 Orchestration: Airflow DAG authored but not executed **[SUBSTITUTION]**
Airflow is not installable. The DAG file is written against the Airflow 2 API with real task dependencies but **cannot be run here**. The same pipeline stages execute via `python -m orbit.pipeline.run` with checkpointing, retries and idempotency, which *is* tested. dbt models are authored for Postgres; `dbt run` is not executed here.

## D-008 Inventory data is simulated from a baseline policy
No inventory data exists in M5. Inventory snapshots, purchase orders, supplier lead times and replenishment events come from simulating a documented days-of-cover baseline policy against demand, with supplier lead-time and delay distributions that are explicit assumptions. Stockout labels therefore come from this simulation (disclosed in MODEL_CARD).

## D-009 M5 units treated as uncensored demand
Real retail sales are censored by stockouts. M5 does not tell us when stock-outs occurred. We treat units sold as demand. This biases real-data demand estimates low in reality.

## D-010 Forecast target and leakage control
Direct multi-horizon target: total units over days (t, t+h]; features use only data up to day t, plus calendar facts known in advance. Training origins must satisfy `origin + h <= test_start` (purge gap equal to horizon). Rolling-origin folds are expanding-window.

## D-011 Optimiser formulation
Per-item newsvendor-style expected cost with fixed ordering cost, solved over the relaxed problem with SciPy SLSQP under coupled budget/supplier-capacity/storage constraints, then rounded to pack/MOQ multiples and repaired to satisfy all hard constraints. Feasibility is asserted by an independent validator.

## D-012 Optimiser redesigned after seeing the first backtest (disclosure)
ORBIT v1 (continuous base-stock ordering every review) cost $77.4k vs a tuned incumbent at $45.6k, driven by $50.6k of ordering cost from 4,218 orders. I then added capacity-aware (s,S) EOQ batching (cap: 60 days of demand). The final ORBIT ($42.9k) is therefore *tuned after observing the test window's results*, which favours it. Both results are kept in `simulation.json` and the UI. Mitigations: the incumbent is also tuned (on the 365 days *before* the window, plus a hindsight frontier evaluated on the window), and the forecast ablation is reported.

## D-013 Straw-man baseline replaced
The first stockout baseline ("low inventory-position cover") scored ROC-AUC 0.42 (worse than chance) because position includes on-order stock. A fairer baseline (on-hand cover, PR-AUC 0.70) is the comparison of record; the straw man is shown only as an illustration.

## D-014 Service-mode rounding
Cost-based rounding skipped needed orders in service mode (fill rate 65%). Service mode now uses rule-based (round-up) rounding; cost mode keeps cost-based rounding. Verified in the backtest (ORBIT service-95: fill 99.6%).

## D-015 API layer
Per-request read-only SQLite connections; process-level cache of the demand matrix and a "position" table (inventory + forecasts + supplier terms) built on first use. Optimiser inputs for the API use the stored forecasts: 14-day for protection periods ≤ 17.5 days, otherwise 28-day. Defaults for supplier/storage capacity come from the dimension tables; storage already used by items outside the plan is deducted.

## D-016 Assistant design
Template-first, no LLM required. The optional LLM provider (needs `ANTHROPIC_API_KEY` and `ORBIT_LLM_MODEL`) is unverified live; its output is used only if every number and citation appears in the evidence.

## D-017 dbt and Postgres verification method
dbt could not be installed. The model SQL was rendered with Jinja (`source`/`ref` replaced) and executed on a local PostgreSQL 16 cluster loaded with the full dataset; this checks SQL validity and produced sensible row counts, but dbt itself (tests, lineage, materialisation) was not run.

## D-018 Real-M5 hardening and an unresolved optimiser weakness
After a user ran the pipeline on real M5 files (subset of 3 stores x 8 items per department): calendar truncated to observed sales days, baselines served when they win model selection, Windows-safe memory reporting and SQLite URIs, a DC fallback for unknown store ids, and optional subsetting (`ORBIT_STORES`, `ORBIT_ITEMS_PER_DEPT`). The optimiser weakness under a binding storage cap is documented in KNOWN_LIMITATIONS #26 and is **not** fixed; changing it after seeing these results would be another post-hoc design change and must be disclosed as such.
