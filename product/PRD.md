# ORBIT — Product Requirements Document

*Status: portfolio project; built against simulated data. No customer has used this. Personas and pain points below are hypotheses drawn from public knowledge of retail planning, not from interviews (see USER_RESEARCH.md).*

## Problem
Replenishment planners at multi-store retailers juggle forecasts in one tool, stock in another and supplier reliability in a third. Decisions default to heuristics (days-of-cover rules) that ignore uncertainty, supplier variability and shared constraints (budget, supplier capacity, storage). Outcomes: stockouts on some items and excess stock on others, with little visibility into why a recommendation was made.

## Goal
One place where a planner can (1) see what will sell, (2) see what is at risk of running out, (3) get a constrained, explainable order recommendation, and (4) test "what if" before committing.

## Target users (hypothesis)
Replenishment planner (primary), supply-chain manager (secondary), category/merchandising analyst (consumer of forecasts). See PERSONAS.md.

## Scope (v1, built)
Control Tower; Demand Intelligence; Inventory; Supplier Intelligence; Replenishment Planner; Scenario Simulator; grounded AI Assistant; API; data pipeline with quality gates.
## Non-goals (v1)
Pricing/promotion optimisation, multi-echelon network optimisation, automated PO submission to ERP, multi-user permissions, real-time streaming data, substitution/cannibalisation.

## Functional requirements
| ID | Requirement | Status |
|---|---|---|
| F1 | 7/14/28-day forecasts with uncertainty and out-of-sample evidence | Built (simulated data) |
| F2 | Stockout probability per product-store, ranked by estimated exposure | Built |
| F3 | Order recommendation respecting pack, MOQ, budget, supplier and storage caps, with a plain-language reason per line | Built |
| F4 | Scenario comparison (demand, lead time, budget, capacity) | Built |
| F5 | Supplier scorecards incl. terms history | Built |
| F6 | Assistant answers only from platform data with citations; works without an LLM | Built |
| F7 | Every simulated figure labelled simulated | Built |
## Non-functional
Responses < 100 ms p95 at demo scale (measured ≈ 3–11 ms in-process); pipeline idempotent and recoverable; no silent data loss (quarantine); keyboard-reachable navigation and tables; light/dark themes.

## Success metrics → METRICS.md. Experiment design → EXPERIMENT_PLAN.md. Prioritisation → RICE.md.

## Key trade-offs and decisions
1. **Trust over breadth**: seven screens, every number traceable to a query; unsupported assistant questions are refused rather than guessed.
2. **Honest value claim**: the backtest shows cost reduction vs an untuned incumbent mainly from batching and retuned cover levels, at higher working capital; versus a tuned incumbent the gain is small. The product should not be sold on "AI forecasting" alone (see the ablation in MODEL_CARD.md).
3. **Optimiser over forecast polish**: forecast accuracy gains of a few WAPE points barely moved cost; investment should favour decision quality, constraints and supplier data.

## Risks
Real-world data quality (censored demand, wrong lead times); planner trust and adoption; unmodelled promotions; simulated results not transferring to real data. Mitigations: show uncertainty and reasons, keep human approval, run a shadow-mode pilot before any live use.

## Open questions
What do planners actually override and why? What cost parameters do real buyers use? Is ordering cost or working capital the binding concern? None answered yet — requires user research.
