# Test results (run 2026-11-05 in the build sandbox)

## Automated suite — `python -m pytest tests`: **74 passed** (71 original + 3 real-data regression tests added later), 0 failed
| File | Tests | Covers |
|---|---|---|
| test_pipeline.py | 8 | full pipeline load (168×1941 rows), zero DQ failures, FK integrity, no negative inventory, demand = fulfilled + lost, SCD2 continuity, **idempotent re-run**, **recovery after injected failure** (partial state then identical final counts), checkpoint keyed by input hash |
| test_quality.py | 6 | each data-quality check detects its defect (schema, null, duplicate, negative, fractional, referential, date gap, outlier flag, negative inventory) |
| test_features.py | 4 | **future-invariance**: corrupting all post-origin demand/prices leaves features unchanged; target equals the future sum; origins leave room for targets; calendar covers serving window |
| test_optimiser.py | 13 | feasibility, pack/MOQ, zero budget (binding reported), tight budget, supplier capacity, storage capacity, infeasible storage, zero demand, ample stock, monotonicity in demand and stockout cost, service-mode target, safety-stock validation and method switch |
| test_api.py | 34 | all endpoints 200; 404/422 uniform errors; SQL-injection strings; oversize body 413; malformed JSON; auth 401/200; missing warehouse → 503 (not 500); recommend/scenario constraints; assistant citations, grounding, refusal of unsupported/hostile prompts, rejection of ungrounded LLM output |
| test_reliability.py | 6 | registry tamper detection (SHA-256), missing model error, simulator determinism, engine invariants on built data, stored metrics support the documented claims (beats seasonal naive; interval coverage 0.75–0.85; purge = horizon), backtest label and tuned-baseline not on grid boundary |
Tests that read `./data` are skipped (not failed) if the warehouse has not been built; here it was built, so none skipped.
`ruff check .` → all checks passed (E501/E702/B905 deliberately ignored; see pyproject.toml).

## End-to-end UI check — `node scripts/e2e_ui.js` (headless Chromium against the running server): **14/14 checks ok**
Banner visible; all 7 screens render; planner returns rows; scenario run and backtest labelled as simulation; assistant answer carries citations; no horizontal scroll at 390 px; **no console errors; no failed API requests**. Screenshots: `docs/screenshots/`.
This is a script, not part of pytest, and checks structure rather than pixel appearance.

## Pipeline and data
Clean run: 12.4 s, 0 DQ failures, 326,088 fact_sales/fact_inventory rows, 16,724 POs. Idempotent re-run skipped 65/65 batches. Failure injection at `gold_fact_sales/2013-06` then re-run: 36 processed, 29 skipped, identical final counts (also asserted in the test suite).

## PostgreSQL 16
`sql/postgres/001_schema.sql` created 19 tables in a local PG16 cluster; `scripts/load_postgres.py` loaded all nine warehouse tables with matching row counts (constraints enforced). dbt model SQL (rendered, not run through dbt) executed on that data: mart_inventory_health 168 rows, mart_supplier_scorecard 8 rows, mart_sales_weekly 2,502 rows.

## Not tested / not executed
Airflow DAG (syntax-checked only), dbt itself, Docker build/compose (YAML parses), Terraform, frontend type-check, load/concurrency testing, a live LLM provider, real M5 data, real users.
