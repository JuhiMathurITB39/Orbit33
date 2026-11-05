# API documentation (v1)

Base path `/api/v1`. A machine-readable route list is at `/api/v1/openapi.json` (minimal OpenAPI skeleton: paths and methods only; request/response models are the Pydantic classes in `orbit/api/schemas.py`).

**Auth**: if `ORBIT_API_KEY` (or `ORBIT_API_KEYS`, comma-separated) is set, every route except `/health` needs header `X-API-Key`. Keys are compared in constant time. With no key configured the API is open (development mode). This is an architecture-level control, not a full user/role system.

**Errors** always `{"error": {"code", "message"}}`: 400 `bad_request`, 401 `unauthorized`, 404 `not_found`, 413 `payload_too_large`, 422 `validation_error`, 503 `data_unavailable`, 500 `internal_error` (no internals leaked; request id logged).
Unknown query parameters are rejected (422).

| Method & path | Purpose | Key parameters |
|---|---|---|
| GET /health | liveness + data readiness (public) | |
| GET /meta | data kind, as-of date, disclaimer | |
| GET /products | search products | `q`, `cat_id`, `limit` (≤500), `offset` |
| GET /stores | stores and their DCs | |
| GET /sales/history | history for a series | `item_id`, `store_id`, `start`, `end`, `granularity=day|week` |
| GET /forecast | 7/14/28-day point + 80% interval | `item_id`, `store_id` |
| GET /forecast/backtest | out-of-sample predictions vs actuals | `item_id`, `store_id`, `horizon=7|14|28` |
| GET /models/comparison | model metrics, stockout/supplier model metrics, registry | |
| GET /demand/seasonality | day-of-week and monthly profile | `item_id`, `store_id` |
| GET /stockout-risk | calibrated 7-day stockout probabilities | `store_id`, `min_prob`, `sort=prob|revenue_at_risk|cover_days` |
| GET /inventory | positions, safety stock, reorder point | `store_id`, `item_id`, `q` |
| GET /suppliers, /suppliers/{id} | supplier scorecards, SCD2 terms, monthly performance | |
| GET /purchase-orders | PO list | `status`, `supplier_id` |
| POST /replenishment/recommend | constrained order plan with reasons | body: `store_id`, `item_ids?`, `assumptions`, `include_sensitivity` |
| POST /scenarios/run | up to 8 named what-if scenarios | body: `store_id`, `scenarios[]` |
| GET /simulation | stored historical policy backtest | |
| GET /kpis | control-tower KPIs and priority actions | |
| POST /assistant/ask | grounded Q&A with citations | body: `question` (≤500 chars), optional `item_id`, `store_id` |

`assumptions` fields (all optional, bounded): `holding_rate_annual`, `order_cost`, `stockout_cost_multiplier`, `goodwill`, `service_level` (0.5–0.9999), `mode` (`cost`|`service`), `batching`, `review_days`, `budget`, `storage_capacity_volume`, `supplier_capacity_units`, `demand_multiplier`, `lead_time_add_days`.

Example:
```bash
curl -s -X POST localhost:8000/api/v1/replenishment/recommend -H 'content-type: application/json' \
  -d '{"store_id":"CA_1","item_ids":["FOODS_1_001"],"assumptions":{"budget":500}}'
```
Run locally: `python -m orbit.api.app` (serves the built frontend from `frontend/dist` too).
