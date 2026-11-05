"""Serving tables: precomputed model outputs read by the API (kept separate from the star schema)."""

SERVING_DDL = """
CREATE TABLE IF NOT EXISTS serving_forecast (
  item_id TEXT NOT NULL, store_id TEXT NOT NULL, horizon INTEGER NOT NULL, as_of_date TEXT NOT NULL,
  point REAL NOT NULL, lo REAL NOT NULL, hi REAL NOT NULL, model TEXT NOT NULL, model_run TEXT NOT NULL,
  PRIMARY KEY (item_id, store_id, horizon)
);
CREATE TABLE IF NOT EXISTS serving_backtest (
  item_id TEXT NOT NULL, store_id TEXT NOT NULL, horizon INTEGER NOT NULL, origin_date TEXT NOT NULL,
  model TEXT NOT NULL, role TEXT NOT NULL, y REAL NOT NULL, pred REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_backtest_series ON serving_backtest (item_id, store_id, horizon, model);
CREATE TABLE IF NOT EXISTS serving_stockout_risk (
  item_id TEXT NOT NULL, store_id TEXT NOT NULL, as_of_date TEXT NOT NULL, prob REAL NOT NULL, flagged INTEGER NOT NULL,
  threshold REAL NOT NULL, on_hand REAL, on_order REAL, due7_promised REAL, overdue_qty REAL, cover_days REAL,
  pos_cover_days REAL, m28 REAL, sup_lead_mean REAL, sup_late_rate REAL, forecast7 REAL, projected_shortfall_units REAL,
  revenue_at_risk REAL, model_run TEXT NOT NULL, PRIMARY KEY (item_id, store_id)
);
CREATE TABLE IF NOT EXISTS serving_supplier_risk (
  supplier_id TEXT PRIMARY KEY, as_of_date TEXT NOT NULL, recent_late_rate REAL, recent_lead_mean REAL,
  recent_lead_sd REAL, contract_lead_mean REAL, pred_late_prob REAL, open_pos INTEGER, overdue_pos INTEGER,
  n_obs INTEGER, risk_level TEXT NOT NULL, model_run TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS serving_metrics (key TEXT PRIMARY KEY, json TEXT NOT NULL, created_at TEXT NOT NULL);
"""
