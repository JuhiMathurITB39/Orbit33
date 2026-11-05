"""Star-schema DDL (SQLite runtime). A PostgreSQL 16 version lives in sql/postgres/01_schema.sql.

Grain of each fact table
  fact_sales            one row per product x store x day (units sold at the posted price)
  fact_inventory        one row per product x store x day (end-of-day inventory snapshot, SIMULATED)
  fact_purchase_orders  one row per purchase-order line (product x store x order day, SIMULATED)
  fact_replenishment    one row per replenishment decision by a policy (product x store x review day, SIMULATED)

dim_supplier is Slowly Changing Dimension Type 2: one row per (supplier_id, version) with valid_from/valid_to.
Facts reference the version valid on the order date.
"""

SQLITE_DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS dim_date (
  date_key      INTEGER PRIMARY KEY,           -- yyyymmdd
  date          TEXT NOT NULL UNIQUE,
  d_idx         INTEGER NOT NULL UNIQUE,       -- 0-based day index (M5 d_1 -> 0)
  wm_yr_wk      INTEGER NOT NULL,
  weekday       TEXT NOT NULL,
  wday          INTEGER NOT NULL CHECK (wday BETWEEN 1 AND 7),
  month         INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
  quarter       INTEGER NOT NULL CHECK (quarter BETWEEN 1 AND 4),
  year          INTEGER NOT NULL,
  is_weekend    INTEGER NOT NULL CHECK (is_weekend IN (0,1)),
  event_name_1  TEXT, event_type_1 TEXT, event_name_2 TEXT, event_type_2 TEXT,
  snap_ca INTEGER NOT NULL DEFAULT 0, snap_tx INTEGER NOT NULL DEFAULT 0, snap_wi INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS dim_warehouse (
  warehouse_key INTEGER PRIMARY KEY AUTOINCREMENT,
  warehouse_id  TEXT NOT NULL UNIQUE,
  name          TEXT NOT NULL,
  region        TEXT NOT NULL,
  storage_capacity_units REAL NOT NULL CHECK (storage_capacity_units > 0),
  is_simulated  INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS dim_store (
  store_key     INTEGER PRIMARY KEY AUTOINCREMENT,
  store_id      TEXT NOT NULL UNIQUE,
  state_id      TEXT NOT NULL,
  warehouse_key INTEGER NOT NULL REFERENCES dim_warehouse(warehouse_key),
  storage_capacity_volume REAL NOT NULL CHECK (storage_capacity_volume > 0)
);

CREATE TABLE IF NOT EXISTS dim_supplier (
  supplier_key  INTEGER PRIMARY KEY AUTOINCREMENT,
  supplier_id   TEXT NOT NULL,
  name          TEXT NOT NULL,
  version       INTEGER NOT NULL,
  valid_from    TEXT NOT NULL,
  valid_to      TEXT NOT NULL,
  is_current    INTEGER NOT NULL CHECK (is_current IN (0,1)),
  mean_lead_days REAL NOT NULL CHECK (mean_lead_days > 0),
  lead_sd_days   REAL NOT NULL CHECK (lead_sd_days >= 0),
  on_time_prob   REAL NOT NULL CHECK (on_time_prob BETWEEN 0 AND 1),
  weekly_capacity_units REAL NOT NULL CHECK (weekly_capacity_units > 0),
  is_simulated  INTEGER NOT NULL DEFAULT 1,
  UNIQUE (supplier_id, version)
);

CREATE TABLE IF NOT EXISTS dim_product (
  product_key   INTEGER PRIMARY KEY AUTOINCREMENT,
  item_id       TEXT NOT NULL UNIQUE,
  dept_id       TEXT NOT NULL,
  cat_id        TEXT NOT NULL,
  supplier_id   TEXT NOT NULL,                 -- business key of dim_supplier
  unit_cost     REAL NOT NULL CHECK (unit_cost > 0),   -- SIMULATED
  ref_price     REAL NOT NULL CHECK (ref_price > 0),   -- median observed sell price
  pack_size     INTEGER NOT NULL CHECK (pack_size > 0),-- SIMULATED
  moq_units     INTEGER NOT NULL CHECK (moq_units > 0),-- SIMULATED
  unit_volume   REAL NOT NULL CHECK (unit_volume > 0)  -- SIMULATED
);

CREATE TABLE IF NOT EXISTS fact_sales (
  date_key    INTEGER NOT NULL REFERENCES dim_date(date_key),
  product_key INTEGER NOT NULL REFERENCES dim_product(product_key),
  store_key   INTEGER NOT NULL REFERENCES dim_store(store_key),
  units_sold  INTEGER NOT NULL CHECK (units_sold >= 0),
  sell_price  REAL,
  sales_amount REAL,
  is_outlier  INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (date_key, product_key, store_key)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_fact_sales_series ON fact_sales (product_key, store_key, date_key);
CREATE INDEX IF NOT EXISTS ix_fact_sales_store_date ON fact_sales (store_key, date_key);

CREATE TABLE IF NOT EXISTS fact_inventory (
  date_key    INTEGER NOT NULL REFERENCES dim_date(date_key),
  product_key INTEGER NOT NULL REFERENCES dim_product(product_key),
  store_key   INTEGER NOT NULL REFERENCES dim_store(store_key),
  on_hand_end REAL NOT NULL CHECK (on_hand_end >= 0),
  on_order_end REAL NOT NULL CHECK (on_order_end >= 0),
  units_received REAL NOT NULL CHECK (units_received >= 0),
  units_demanded REAL NOT NULL CHECK (units_demanded >= 0),
  units_fulfilled REAL NOT NULL CHECK (units_fulfilled >= 0),
  lost_sales_units REAL NOT NULL CHECK (lost_sales_units >= 0),
  stockout_flag INTEGER NOT NULL CHECK (stockout_flag IN (0,1)),
  PRIMARY KEY (date_key, product_key, store_key)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_fact_inv_series ON fact_inventory (product_key, store_key, date_key);

CREATE TABLE IF NOT EXISTS fact_purchase_orders (
  po_id        TEXT PRIMARY KEY,
  product_key  INTEGER NOT NULL REFERENCES dim_product(product_key),
  store_key    INTEGER NOT NULL REFERENCES dim_store(store_key),
  supplier_key INTEGER NOT NULL REFERENCES dim_supplier(supplier_key),
  warehouse_key INTEGER NOT NULL REFERENCES dim_warehouse(warehouse_key),
  order_date_key    INTEGER NOT NULL REFERENCES dim_date(date_key),
  promised_date_key INTEGER,
  received_date_key INTEGER,                    -- NULL while the order is still open at the end of history
  qty_ordered  REAL NOT NULL CHECK (qty_ordered > 0),
  qty_received REAL CHECK (qty_received >= 0),
  unit_cost    REAL NOT NULL CHECK (unit_cost > 0),
  status       TEXT NOT NULL CHECK (status IN ('open','received','partial')),
  promised_lead_days INTEGER NOT NULL,
  actual_lead_days   INTEGER,
  delay_days   INTEGER,
  is_late      INTEGER
);
CREATE INDEX IF NOT EXISTS ix_po_supplier_date ON fact_purchase_orders (supplier_key, order_date_key);
CREATE INDEX IF NOT EXISTS ix_po_series ON fact_purchase_orders (product_key, store_key, order_date_key);
CREATE INDEX IF NOT EXISTS ix_po_status ON fact_purchase_orders (status);

CREATE TABLE IF NOT EXISTS fact_replenishment (
  replenishment_id INTEGER PRIMARY KEY AUTOINCREMENT,
  date_key    INTEGER NOT NULL REFERENCES dim_date(date_key),
  product_key INTEGER NOT NULL REFERENCES dim_product(product_key),
  store_key   INTEGER NOT NULL REFERENCES dim_store(store_key),
  policy      TEXT NOT NULL,
  inventory_position REAL NOT NULL,
  qty_ordered REAL NOT NULL CHECK (qty_ordered >= 0),
  po_id       TEXT REFERENCES fact_purchase_orders(po_id)
);
CREATE INDEX IF NOT EXISTS ix_repl_series ON fact_replenishment (product_key, store_key, date_key);

-- Operational tables
CREATE TABLE IF NOT EXISTS etl_checkpoint (
  stage TEXT NOT NULL, batch_key TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('running','succeeded','failed')),
  input_hash TEXT, rows_in INTEGER, rows_out INTEGER, rows_rejected INTEGER, duplicates INTEGER,
  duration_s REAL, attempts INTEGER NOT NULL DEFAULT 0, started_at TEXT, finished_at TEXT, error TEXT,
  PRIMARY KEY (stage, batch_key)
);
CREATE TABLE IF NOT EXISTS dq_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, layer TEXT NOT NULL, table_name TEXT NOT NULL,
  check_name TEXT NOT NULL, passed INTEGER NOT NULL, severity TEXT NOT NULL, n_checked INTEGER, n_failed INTEGER,
  detail TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pipeline_runs (
  run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, data_kind TEXT,
  metrics_json TEXT
);
CREATE TABLE IF NOT EXISTS data_lineage (
  output TEXT NOT NULL, input TEXT NOT NULL, stage TEXT NOT NULL, run_id TEXT NOT NULL,
  PRIMARY KEY (output, input, stage)
);
CREATE TABLE IF NOT EXISTS meta_kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
