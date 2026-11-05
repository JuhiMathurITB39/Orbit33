# Experiment plan (design only — not run)

**Question**: does ORBIT's constrained recommendation improve service-adjusted inventory cost versus the planners' current rule, without lowering fill rate?
**Design**: store-level randomised (or matched) pilot after a 4-week shadow period in which recommendations are logged but not used. Unit: store × category (to avoid contamination across a shared supplier order). Arms: control (current rule) vs ORBIT-assisted. Duration ≥ 8 weeks (several replenishment cycles; longer if lead times > 2 weeks).
**Primary metric**: service-adjusted inventory cost per unit demanded. **Guardrails**: fill rate (non-inferiority margin −1 pt), average inventory value (≤ +10%), planner time per order cycle.
**Power**: needs real variance estimates; as a placeholder, simulate using the backtest's replication spread (5 replications is too few; use ≥ 30) — not computed.
**Risks**: interference through shared suppliers; planner learning/Hawthorne effects; seasonality; promotions; low store count → use switchback or difference-in-differences.
**Decision rule (pre-registered)**: ship if the primary metric improves by ≥ 3% with a 95% CI excluding 0 and no guardrail breached.
