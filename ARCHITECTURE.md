# ORBIT architecture

```
 M5-schema CSVs ──► BRONZE (gzip CSV, per month, hash-checked)
 (simulated; real M5      │  quarantine of rejected rows, per-batch checkpoint
  via ORBIT_M5_DIR)       ▼
                       SILVER (typed, validated, deduped) ──► DQ checks (dq_results)
                          │
          supply simulator (CRN, SCD2 suppliers, DCs, capacity)   ← SYNTHETIC inventory / PO / supplier data
                          ▼
                 GOLD star schema (SQLite runtime; PostgreSQL DDL provided)
     dim_date  dim_product  dim_store  dim_supplier(SCD2)  dim_warehouse
     fact_sales  fact_inventory  fact_purchase_orders  fact_replenishment
                          │
        ┌─────────────────┼───────────────────────────┐
        ▼                 ▼                           ▼
  ml/ (forecast,     optim/ (safety stock,        dbt_project (marts;
  stockout, supplier) replenishment, backtest)    SQL verified on PG16)
        │  registry (SHA-256)   │
        ▼                       ▼
   serving_* tables  ──►  api/ (Starlette, /api/v1) ──► assistant/ (grounded, template-first)
                                  │
                                  ▼
                         frontend/ (React/TS, SVG charts, 7 screens)
```

## Layers and guarantees
- **Bronze/silver/gold** live in `orbit/pipeline/stages.py`. Work is split into month batches; each batch has a checkpoint keyed by `(stage, batch, input_hash)` plus a logic-version constant for the supply stage. A changed input or changed logic is reprocessed; an unchanged one is skipped. Files are written atomically (temp + rename). Gold loads are delete-then-insert in one transaction, so a crash leaves either the old or the new batch.
- **Data quality**: schema, null, uniqueness, non-negativity, integer-quantity, referential, date-contiguity, date-order and inventory checks produce rows in `dq_results`. Bad rows go to quarantine rather than being silently dropped.
- **Supply simulator** (`data/supply_sim.py`): a day-by-day engine with common random numbers keyed by (series, order day), so two policies see identical supplier randomness. Hard caps (storage, supplier capacity) are enforced by the engine itself, not just by policies.
- **Forecasting** (`ml/`): direct multi-horizon models, leakage-safe features, rolling-origin folds with a purge gap, split-conformal intervals, registry with checksum verification.
- **Optimiser** (`optim/replenish.py`): expected-cost minimisation with Gamma protection-period demand; closed form → (s,S) EOQ batching → SLSQP relaxation → rounding → greedy repair → independent `validate_plan`.
- **API** (`api/`): strict Pydantic validation (`extra="forbid"`, ID whitelist), parameterised SQL only, read-only DB connections per request, uniform error envelope, optional API-key auth, 64 KB body cap, 503 (not 500) when data is not ready.
- **Assistant**: intent → fixed tool functions → numbered evidence → template; optional LLM rewrite is accepted only if every number and citation is in the evidence.

## Environment-forced substitutions (full rationale in DECISIONS.md)
Starlette instead of FastAPI · SQLite runtime with PostgreSQL DDL · gzip CSV instead of Parquet · sklearn HistGradientBoosting instead of LightGBM/XGBoost · file registry instead of MLflow · esbuild + hand-built SVG charts instead of Vite/Recharts · Airflow DAG and dbt models authored but not run under their own runtimes · Docker and Terraform authored only.

## Scaling notes (not implemented)
Swap SQLite for PostgreSQL/Redshift via the provided DDL; move the lake to S3 Parquet; replace per-process caches with a shared cache; run the pipeline and training under Airflow; replace the file registry with MLflow. The service layer already isolates SQL in `api/services.py`.
