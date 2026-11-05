# Model card

All numbers: **simulated M5-schema data**; reproduce with `python -m orbit.ml.train` (results stored in `serving_metrics`, `data/artifacts`). They are not M5 results and should not be quoted as real-world accuracy. Simulated demand comes from a known generator, so forecasting here is probably easier than on real data.

## 1. Demand forecast
- **Task**: total units over the next 7/14/28 days per product-store (168 series). **Use**: feed replenishment and risk screens. **Not for**: price setting, new-product launches, promotions beyond the calendar events present in training.
- **Features**: lags/rolling sums of demand at 7/14/28/56 days, price ratios, calendar facts known in advance (weekday, month, events, SNAP), series scale; target divided by series level. No future information.
- **Candidates**: seasonal naive, moving average, linear regression, random forest, HistGradientBoosting (stand-in for LightGBM/XGBoost).
- **Evaluation**: expanding rolling-origin, 4 folds × 13 weeks, folds 0–2 validation, fold 3 untouched test, purge gap = horizon. **Selection**: simplest model within 2% of the best validation WAPE.

| Horizon | Seasonal naive | Moving avg | Linear reg. | **Random forest (selected)** | HGB |
|---|---|---|---|---|---|
| 7 d test WAPE | 31.5% | 23.1% | 23.7% | **20.5%** | 20.4% |
| 14 d | 24.5% | – | – | **16.4%** | 16.3% |
| 28 d | – | – | – | **14.1%** | 13.9% |
Gains over the 28-day moving-average baseline are modest (~2.6 WAPE points at 7 days). Bias on test ≈ +0.4% (7 d). 80% split-conformal intervals covered 81–82% of test cases (nominal 80%).
- **Limitations**: single test fold (13 weeks); no real promotions or price shocks beyond the simulator; intervals assume stable error distribution; one random seed.

## 2. Stockout-risk classifier
- **Target**: any lost sales within the next 7 days, **derived from the simulation** (not observed). Features: on-hand and on-order cover, overdue and due quantities, trailing demand, supplier late rate and lead time. Isotonic calibration on validation; threshold chosen by cost (false negative 5 : false positive 1 — an assumption).
- **Test (prevalence 23.7%)**: fair baseline "on-hand cover" PR-AUC 0.697 / ROC-AUC 0.904; logistic regression 0.770 / 0.929; **random forest (selected) 0.808 / 0.938**; HGB 0.807 / 0.938. ECE after calibration ≈ 0.02 (0.11 before for the logistic model). At the chosen threshold the forest has recall 0.89, precision 0.64.
- **Caveat**: labels come from the same simulator that generates the features, so the lift over the baseline is likely optimistic for real operations. An earlier "low position cover" baseline scored ROC 0.42 (worse than chance) and was a straw man; it is kept only as an illustration.

## 3. Supplier late-delivery model
- **Target**: delivery ≥ 2 days late. Features only from deliveries that had arrived before the order date. Test prevalence 21.6%.
- **Result: weak signal.** Recent-late-rate baseline PR-AUC 0.288 / ROC 0.613; logistic regression (selected) 0.307 / 0.636; HGB 0.301 / 0.648. This is at best a ranking aid; the risk level shown in the UI also uses the recent observed late rate. Lateness in the simulator is mostly random noise around supplier terms, so low skill is expected.

## 4. Replenishment optimiser (not ML) — see BENCHMARKS and the Scenario Simulator
Historical backtest (365 days, 5 replications varying supplier randomness only, **one demand path**): default incumbent $127.8k total cost; tuned incumbent $45.6k; ORBIT $42.9k (−5.8% vs tuned, −66% vs default) but with ~3.7× the average inventory value of the default policy ($86.9k vs $23.7k) and 8.6% more working capital than the tuned incumbent ($80.0k). Replacing the ML forecast with a trailing 28-day mean changes ORBIT's cost by only 0.6%: **the forecast model contributes little to decision cost here; most of the gain over the default policy comes from retuned cover and EOQ batching.** The EOQ batching was added after seeing the first backtest (v1 base-stock cost $77.4k is kept). ORBIT optimises the same cost function the evaluation measures; the incumbent does not.

## Ethical / operational notes
No personal data. Recommendations are advisory; a planner should review them. Failure modes: supplier regime change, unmodelled promotions, censored demand on real data.
