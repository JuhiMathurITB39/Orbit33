# Metrics framework

**North star**: service-adjusted inventory cost — total (holding + ordering + lost-sales) cost per unit demanded, for the product-store pairs under ORBIT management. Chosen because optimising only service or only inventory is trivially gameable.

| Layer | Metric | Definition | Guardrail / note |
|---|---|---|---|
| Outcome | Fill rate | fulfilled units / demanded units | must not fall vs control |
| Outcome | Inventory value (avg) | avg on-hand × unit cost | guardrail: working capital |
| Outcome | Stockout-day rate | share of product-days with lost sales | |
| Decision quality | Recommendation acceptance rate | accepted without edit / shown | needs override capture (not built) |
| Decision quality | Override magnitude | median |edit| / recommended | |
| Model | Forecast WAPE per horizon, bias, interval coverage | see MODEL_CARD | monitor drift weekly |
| Model | Stockout PR-AUC and calibration (ECE) | | alert if ECE > 0.05 |
| Data | DQ check pass rate, quarantined rows, pipeline success | `dq_results`, `pipeline_runs` | |
| Product | Weekly active planners; time to build weekly order | needs instrumentation (not built) | |
| Trust | % assistant answers with citations; % refused as unsupported | | |
Instrumentation exists today only for model and data metrics. Product-usage and decision-quality metrics would need event logging (NEXT_STEPS.md).
