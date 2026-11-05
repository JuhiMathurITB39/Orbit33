# Amazon Data Engineer Intern — how to present ORBIT

**What the role tests**: SQL, data modelling, ETL design, data quality, scale thinking, ownership, Leadership Principles.
**Strengths you can prove**: medallion pipeline with per-batch, hash-keyed checkpoints; idempotent re-run (65/65 batches skipped); recovery after an injected failure (36 processed, 29 skipped, identical counts); quarantine instead of silent drops; star schema with SCD2 supplier dimension; PostgreSQL 16 DDL loaded with the full data (row counts matched); dbt-style marts whose SQL ran on PG16; EXPLAIN plans stored; 71 tests incl. data-quality and recovery.
**Gaps to own**: SQLite runtime, no Spark/Glue/Redshift/S3 in practice; Airflow DAG and dbt were never run under their own runtimes; Docker/Terraform never executed; scale is 326 k fact rows (12 s) — extrapolating to billions is speculation.

## Deep-dive topics
- *Idempotency*: checkpoint key = (stage, batch, input hash) plus logic version; gold load = delete-then-insert in one transaction; atomic file writes.
- *SCD2*: dim_supplier with valid_from/valid_to and a partial unique index (PG) for one current row; facts join on supplier_key so history stays correct.
- *Data quality*: checks list in `pipeline/quality.py`; failures recorded in `dq_results`; zero failures on this data, so say the checks were validated by unit tests with bad inputs, not by real dirty data.
- *What breaks at 1000× scale?* Per-series Python loops in the simulator/features, SQLite single writer, CSV lake. Plan: S3 Parquet partitioned by month, Glue/Spark or dbt on Redshift, Airflow orchestration.
- *Slow query*: stockout-days-by-product 345 ms is a full scan; add a covering index or a pre-aggregate.
Leadership Principles stories: **Dive Deep** (supply-stage hash didn't include logic version → changed engine would have been skipped; found and fixed), **Insist on the Highest Standards** (straw-man baseline replaced), **Bias for Action** (substitutions documented rather than blocked), **Earn Trust** (limitations file).
