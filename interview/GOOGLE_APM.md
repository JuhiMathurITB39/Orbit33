# Google APM — how to present ORBIT

**What the role tests**: product sense, analytical judgement, technical fluency, leadership/communication, comfort with ambiguity.
**Honest strengths**: a complete PRD, personas, RICE, metrics, experiment design and roadmap tied to a working product; clear trade-offs backed by evidence (forecast ablation shows the "AI" matters less than the decision layer); explicit non-goals.
**Honest gaps**: no user interviews, no real users, no launched product, no usage data. Do not claim validation. Frame ORBIT as "a product I built to learn a domain, with hypotheses I would test first."

## Story arc (4 minutes)
1. Problem and user (planner) — hypothesis-labelled. 2. Insight: decision quality > forecast accuracy (ablation 0.6%). 3. What I built and deliberately did not. 4. How I'd validate: shadow mode → pilot, metrics and guardrails (EXPERIMENT_PLAN.md). 5. What I'd cut/change if wrong.

## Likely questions → pointers
- *Prioritise the next quarter.* RICE.md (state inputs are judgement; ERP export first because recommendations are otherwise advisory only).
- *How would you measure success?* METRICS.md: north star = service-adjusted inventory cost; guardrails fill rate and working capital.
- *A trade-off you made.* Cost vs working capital: ORBIT's best-cost plan holds 3.7× the default inventory; show the frontier chart in the Scenario Simulator and let the user choose budget.
- *How would you handle a model that is wrong?* Uncertainty bands, reasons per line, human approval, override capture.
- *Design critique.* Control Tower opens with priority actions; every table row links to the planner; simulated banner always visible.
**Rehearse**: demo path Control Tower → click top action → Planner (change lead time) → Simulator → ask the assistant an unsupported question.
