# RICE prioritisation (v2 candidates)

Reach = share of planners affected per quarter (assumed), Impact 0.25/0.5/1/2/3, Confidence %, Effort in person-weeks; RICE = R×I×C/E, shown ×100. **All inputs are judgement calls, not measurements**; use the ranking as a conversation starter.

| Candidate | Reach | Impact | Confidence | Effort | RICE | Rationale |
|---|---|---|---|---|---|---|
| Planner override capture + reason codes (feedback loop) | 90% | 1 | 80% | 3 | 24.0 | cheapest source of ground truth; unlocks learning and trust |
| ERP/PO export (CSV/API) | 80% | 2 | 70% | 4 | 28.0 | closes the "last mile"; without it recommendations stay advisory |
| Real-data connector + censored-demand correction | 100% | 3 | 50% | 8 | 18.8 | biggest accuracy risk; high uncertainty |
| Promotion/price-event features | 60% | 1 | 50% | 5 | 6.0 | M5 has events; real promotions need integration |
| Supplier lead-time learning with hierarchical shrinkage | 70% | 1 | 60% | 4 | 10.5 | current supplier model has weak signal |
| Multi-user auth/RBAC + audit log | 100% | 0.5 | 90% | 5 | 9.0 | prerequisite for team use, little direct value |
| Alerting (email/Slack) on high-risk items | 70% | 1 | 70% | 2 | 24.5 | cheap, visible value |
| Multi-echelon (DC→store) optimisation | 40% | 2 | 40% | 10 | 3.2 | high effort, uncertain need |

Recommended order: ERP export → alerting → override capture → real-data connector → supplier learning.
