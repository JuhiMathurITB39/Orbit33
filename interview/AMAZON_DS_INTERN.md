# Amazon Data Scientist I Intern — how to present ORBIT

**What the role tests**: statistics, ML fundamentals, experiment/causal thinking, evaluation rigour, communication of uncertainty, Leadership Principles.
**Strengths**: leakage-safe features proven by a mutation test; rolling-origin backtests with a purge gap and an untouched test fold; simplest-within-2% selection rule; split-conformal intervals with measured 81–82% coverage; isotonic calibration (ECE 0.11 → 0.01 for the logistic model); a fair vs straw-man baseline discussion; a forecast ablation showing the model barely affects decision cost; common-random-number policy comparisons; a pre-registered-style experiment plan.
**Gaps to own**: simulated data (known generator, labels derived from the simulator → optimistic stockout lift); single test fold; one demand path in the backtest (replications vary supplier noise only, so the sd understates uncertainty); supplier model has weak signal (PR-AUC 0.31 vs 0.29); no LightGBM/XGBoost (sklearn HistGradientBoosting); no causal evidence — nothing was deployed; optimiser design changed after seeing results.

## Deep-dive topics
- *Why WAPE?* Scale-free across series, robust to zeros at aggregate level; bias reported separately; MAPE avoided for intermittent demand.
- *Why direct multi-horizon rather than recursive?* No error accumulation; one model per horizon on level-normalised targets.
- *Interval validity*: conformal needs exchangeability; time series violate it mildly; coverage was measured on the test fold only.
- *Calibration vs ranking*: PR-AUC for ranking, ECE/Brier for probabilities; threshold from asymmetric cost (5:1 is an assumption — show the sensitivity).
- *Why isn't ORBIT's improvement a causal claim?* Same-simulator backtest, policy optimises the evaluated cost, one demand path.
- *How to A/B test it?* EXPERIMENT_PLAN.md: store×category randomisation, shadow period, guardrails, interference via shared suppliers.
Leadership Principles stories: **Are Right, A Lot** (ablation reduced the value claim for ML), **Learn and Be Curious** (service-mode rounding bug → 65% fill), **Earn Trust** (disclosing post-hoc design change).
