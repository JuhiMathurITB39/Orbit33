# Interview guide — ORBIT

Role-specific guides: [Google APM](interview/GOOGLE_APM.md) · [Amazon Data Engineer Intern](interview/AMAZON_DE_INTERN.md) · [Amazon Data Scientist I Intern](interview/AMAZON_DS_INTERN.md).

## 90-second pitch (accurate version)
"ORBIT is a supply-chain decision-support platform I built end-to-end: a medallion data pipeline, a star-schema warehouse, demand and stockout models with leakage-safe rolling-origin backtests, a constrained inventory optimiser, a validated API, a seven-screen UI and a grounded assistant. Because I couldn't download M5 in my environment, demand comes from a simulator with the real M5 schema, and inventory and suppliers are synthetic by design — so every result is labelled simulated. The most useful finding was that the ML forecast barely moved decision cost; the gain came from batching and retuned cover levels, which shaped what I'd build next."

## Say this / don't say this
- Say: "on simulated data", "backtest", "the forecast ablation showed 0.6% difference", "I changed the optimiser after seeing the first backtest and kept the old result".
- Don't say: "saves X% in production", "M5 accuracy", "deployed", "validated with users", "uses LightGBM/MLflow/Airflow/dbt in production" (they were not run).

## Questions every interviewer may ask
1. **Why simulated data?** Network policy blocked Kaggle; the pipeline reads the real M5 schema and swaps in real files via `ORBIT_M5_DIR`. Be ready to say what would change (accuracy would likely be worse; censored demand).
2. **How do you know there's no leakage?** Features use only data ≤ origin plus known-in-advance calendar facts; a test mutates all post-origin data and asserts identical features; purge gap = horizon; the backtest retrains models using only pre-T0 data.
3. **Is 20% WAPE good?** Only relative to baselines: 31.5% seasonal naive, 23.1% moving average. Gain is modest and on a generator I control.
4. **What did the optimiser actually achieve?** −5.8% vs a tuned baseline, −66% vs a default baseline, at higher working capital, one demand path, optimising the cost it is judged on. Say the caveats before they ask.
5. **What would you do differently with real data?** Censored-demand correction, promotions, real lead-time distributions, shadow-mode pilot, override capture.
6. **What failed?** A stockout baseline that scored worse than chance (a straw man I replaced); v1 base-stock ordering cost $50k (led to EOQ batching); service-mode rounding gave 65% fill rate until fixed. See DECISIONS.md.
