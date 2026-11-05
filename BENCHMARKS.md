# Benchmarks

Everything below was measured in this build sandbox (single shared container, no tuning) on the **simulated** M5-schema dataset
(168 product-store series × 1,941 days = 326,088 fact rows). They show the shape of the system, not production capacity.
Reproduce: `python -m orbit.pipeline.run --reset`, `python scripts/load_postgres.py` (PostgreSQL), and the API snippet in `docs/TEST_RESULTS.md`.

## Pipeline (SQLite runtime, `data/artifacts/pipeline_metrics.json`)
| Metric | Value |
|---|---|
| End-to-end runtime (raw → bronze → silver → synth supply → gold), 65 monthly batches | 12.4 s |
| Rows written across all stages | 1,415,433 (≈114 k rows/s) |
| Peak RSS | 336 MB |
| Slowest stage | silver_sales 6.3 s (per-batch validation + quarantine + atomic write) |
| Storage: raw CSV 2.2 MB → bronze 0.5 MB (gzip) → silver 3.7 MB → warehouse DB 37 MB (with indexes) | |
| Idempotent re-run | all 65 batches skipped; identical row counts |
| Recovery after injected failure in `gold_fact_sales/2013-06` | re-run processed 36 batches, skipped 29, identical final counts |

### Query latency (SQLite, median of 7 runs, `EXPLAIN QUERY PLAN` stored in the metrics file)
| Query | Median | Plan |
|---|---|---|
| Single-series history (index on product, store, date) | 2.5 ms | index search |
| Late rate by supplier | 2.9 ms | index scan |
| Daily sales by store-month | 161 ms | index scan + temp b-tree group |
| Stockout days by product | 345 ms | full fact scan (no covering index; a known cheap fix) |

### PostgreSQL 16 validation
`sql/postgres/001_schema.sql` was loaded into a local PostgreSQL 16 cluster with the full dataset via `scripts/load_postgres.py`;
all 9 tables matched SQLite row counts (FK and CHECK constraints enforced, fact tables range-partitioned).
Query medians **including psql process start-up** (so an upper bound): monthly sales by category 97 ms, single-series history 25 ms, supplier late rate 26 ms.
No tuning or EXPLAIN analysis was done on PostgreSQL; the 326 k-row scale is far too small to say anything about large-scale behaviour.

## API (in-process Starlette TestClient: excludes network and TLS; 30 samples each, warm caches)
| Endpoint | p50 | p95 |
|---|---|---|
| GET /health | 2.4 ms | 3.7 ms |
| GET /kpis | 5.3 ms | 7.6 ms |
| GET /forecast (one series) | 2.4 ms | 2.6 ms |
| GET /inventory?limit=25 | 4.4 ms | 4.9 ms |
| GET /sales/history | 4.7 ms | 5.3 ms |
| POST /replenishment/recommend (56 items, constrained) | 9.7 ms | 10.6 ms |
| Cold start to first KPI response (loads world + builds position table) | 1.1 s | |
No concurrency or load test was run. The runtime caches are per process and read-only; see KNOWN_LIMITATIONS.

## Model training (from `train_summary.json`; forecasting time was not captured in the final summary because that run reused existing forecasts)
Stockout + supplier model training: 14.3 s.

## Backtest runtime
`python -m orbit.optim.run_backtest --reps 5`: see `data/artifacts/simulation.json` (`runtime_s`).
