-- ORBIT warehouse DDL for PostgreSQL 16 (star schema). Mirrors orbit/warehouse/schema.py (SQLite runtime).
-- Validated by loading into a local PostgreSQL 16 cluster (see docs/TEST_RESULTS.md). NOT deployed anywhere.
CREATE SCHEMA IF NOT EXISTS orbit;
SET search_path = orbit;

CREATE TABLE dim_date (
  date_key integer PRIMARY KEY, date date NOT NULL UNIQUE, d_idx integer NOT NULL UNIQUE, wm_yr_wk integer NOT NULL,
  weekday text NOT NULL, wday smallint NOT NULL CHECK (wday BETWEEN 1 AND 7), month smallint NOT NULL CHECK (month BETWEEN 1 AND 12),
  quarter smallint NOT NULL CHECK (quarter BETWEEN 1 AND 4), year smallint NOT NULL, is_weekend boolean NOT NULL,
  event_name_1 text, event_type_1 text, event_name_2 text, event_type_2 text,
  snap_ca boolean NOT NULL DEFAULT false, snap_tx boolean NOT NULL DEFAULT false, snap_wi boolean NOT NULL DEFAULT false);

CREATE TABLE dim_warehouse (
  warehouse_key integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY, warehouse_id text NOT NULL UNIQUE, name text NOT NULL,
  region text NOT NULL, storage_capacity_units double precision NOT NULL CHECK (storage_capacity_units > 0),
  is_simulated boolean NOT NULL DEFAULT true);

CREATE TABLE dim_store (
  store_key integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY, store_id text NOT NULL UNIQUE, state_id text NOT NULL,
  warehouse_key integer NOT NULL REFERENCES dim_warehouse, storage_capacity_volume double precision NOT NULL CHECK (storage_capacity_volume > 0));

-- Slowly changing dimension type 2: one row per supplier per terms version.
CREATE TABLE dim_supplier (
  supplier_key integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY, supplier_id text NOT NULL, name text NOT NULL, version integer NOT NULL,
  valid_from date NOT NULL, valid_to date NOT NULL, is_current boolean NOT NULL,
  mean_lead_days double precision NOT NULL CHECK (mean_lead_days > 0), lead_sd_days double precision NOT NULL CHECK (lead_sd_days >= 0),
  on_time_prob double precision NOT NULL CHECK (on_time_prob BETWEEN 0 AND 1), weekly_capacity_units double precision NOT NULL CHECK (weekly_capacity_units > 0),
  is_simulated boolean NOT NULL DEFAULT true, UNIQUE (supplier_id, version), CHECK (valid_from < valid_to));
-- at most one current row per supplier
CREATE UNIQUE INDEX ux_supplier_current ON dim_supplier (supplier_id) WHERE is_current;

CREATE TABLE dim_product (
  product_key integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY, item_id text NOT NULL UNIQUE, dept_id text NOT NULL, cat_id text NOT NULL,
  supplier_id text NOT NULL, unit_cost numeric(10,4) NOT NULL CHECK (unit_cost > 0), ref_price numeric(10,4) NOT NULL CHECK (ref_price > 0),
  pack_size integer NOT NULL CHECK (pack_size > 0), moq_units integer NOT NULL CHECK (moq_units > 0), unit_volume double precision NOT NULL CHECK (unit_volume > 0));

CREATE TABLE fact_sales (
  date_key integer NOT NULL REFERENCES dim_date, product_key integer NOT NULL REFERENCES dim_product, store_key integer NOT NULL REFERENCES dim_store,
  units_sold integer NOT NULL CHECK (units_sold >= 0), sell_price numeric(10,4), sales_amount numeric(14,4), is_outlier boolean NOT NULL DEFAULT false,
  PRIMARY KEY (date_key, product_key, store_key)) PARTITION BY RANGE (date_key);
CREATE TABLE fact_sales_2011_2013 PARTITION OF fact_sales FOR VALUES FROM (20110101) TO (20140101);
CREATE TABLE fact_sales_2014_2016 PARTITION OF fact_sales FOR VALUES FROM (20140101) TO (20170101);
CREATE TABLE fact_sales_default PARTITION OF fact_sales DEFAULT;
CREATE INDEX ix_fact_sales_series ON fact_sales (product_key, store_key, date_key);

CREATE TABLE fact_inventory (
  date_key integer NOT NULL REFERENCES dim_date, product_key integer NOT NULL REFERENCES dim_product, store_key integer NOT NULL REFERENCES dim_store,
  on_hand_end double precision NOT NULL CHECK (on_hand_end >= 0), on_order_end double precision NOT NULL CHECK (on_order_end >= 0),
  units_received double precision NOT NULL CHECK (units_received >= 0), units_demanded double precision NOT NULL CHECK (units_demanded >= 0),
  units_fulfilled double precision NOT NULL CHECK (units_fulfilled >= 0), lost_sales_units double precision NOT NULL CHECK (lost_sales_units >= 0),
  stockout_flag boolean NOT NULL, PRIMARY KEY (date_key, product_key, store_key)) PARTITION BY RANGE (date_key);
CREATE TABLE fact_inventory_2011_2013 PARTITION OF fact_inventory FOR VALUES FROM (20110101) TO (20140101);
CREATE TABLE fact_inventory_2014_2016 PARTITION OF fact_inventory FOR VALUES FROM (20140101) TO (20170101);
CREATE TABLE fact_inventory_default PARTITION OF fact_inventory DEFAULT;
CREATE INDEX ix_fact_inv_series ON fact_inventory (product_key, store_key, date_key);

CREATE TABLE fact_purchase_orders (
  po_id text PRIMARY KEY, product_key integer NOT NULL REFERENCES dim_product, store_key integer NOT NULL REFERENCES dim_store,
  supplier_key integer NOT NULL REFERENCES dim_supplier, warehouse_key integer NOT NULL REFERENCES dim_warehouse,
  order_date_key integer NOT NULL REFERENCES dim_date, promised_date_key integer REFERENCES dim_date, received_date_key integer REFERENCES dim_date,
  qty_ordered double precision NOT NULL CHECK (qty_ordered > 0), qty_received double precision CHECK (qty_received >= 0),
  unit_cost numeric(10,4) NOT NULL CHECK (unit_cost > 0), status text NOT NULL CHECK (status IN ('open','received','partial')),
  promised_lead_days integer NOT NULL, actual_lead_days integer, delay_days integer, is_late boolean);
CREATE INDEX ix_po_supplier_date ON fact_purchase_orders (supplier_key, order_date_key);
CREATE INDEX ix_po_series ON fact_purchase_orders (product_key, store_key, order_date_key);
CREATE INDEX ix_po_open ON fact_purchase_orders (status) WHERE status = 'open';

CREATE TABLE fact_replenishment (
  replenishment_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, date_key integer NOT NULL REFERENCES dim_date,
  product_key integer NOT NULL REFERENCES dim_product, store_key integer NOT NULL REFERENCES dim_store, policy text NOT NULL,
  inventory_position double precision NOT NULL, qty_ordered double precision NOT NULL CHECK (qty_ordered >= 0), po_id text REFERENCES fact_purchase_orders);
CREATE INDEX ix_repl_series ON fact_replenishment (product_key, store_key, date_key);

-- Operational tables
CREATE TABLE etl_checkpoint (stage text NOT NULL, batch_key text NOT NULL, status text NOT NULL, input_hash text, rows_in bigint, rows_out bigint,
  attempts integer NOT NULL DEFAULT 0, error text, started_at timestamptz, finished_at timestamptz, PRIMARY KEY (stage, batch_key));
CREATE TABLE dq_results (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, run_id text NOT NULL, layer text NOT NULL, table_name text NOT NULL,
  check_name text NOT NULL, passed boolean NOT NULL, n_failed bigint NOT NULL DEFAULT 0, detail jsonb, created_at timestamptz NOT NULL DEFAULT now());

-- Serving tables (model outputs read by the API)
CREATE TABLE serving_forecast (item_id text NOT NULL, store_id text NOT NULL, horizon integer NOT NULL, as_of_date date NOT NULL,
  point double precision NOT NULL, lo double precision NOT NULL, hi double precision NOT NULL, model text NOT NULL, model_run text NOT NULL,
  PRIMARY KEY (item_id, store_id, horizon), CHECK (lo <= point AND point <= hi));
CREATE TABLE serving_stockout_risk (item_id text NOT NULL, store_id text NOT NULL, as_of_date date NOT NULL,
  prob double precision NOT NULL CHECK (prob BETWEEN 0 AND 1), flagged boolean NOT NULL, threshold double precision NOT NULL,
  on_hand double precision, on_order double precision, forecast7 double precision, model_run text NOT NULL, PRIMARY KEY (item_id, store_id));

-- Read-only role for the API
DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'orbit_api_ro') THEN CREATE ROLE orbit_api_ro NOLOGIN; END IF; END $$;
GRANT USAGE ON SCHEMA orbit TO orbit_api_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA orbit TO orbit_api_ro;
