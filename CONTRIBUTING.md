# Contributing

```bash
python -m orbit.pipeline.run --reset     # build the warehouse (simulated M5 unless ORBIT_M5_DIR is set)
python -m orbit.ml.train                 # forecasts, stockout, supplier models
python -m orbit.optim.run_backtest       # policy backtest
(cd frontend && node build.mjs)          # frontend bundle
python -m pytest tests                   # 71 tests; ~45 s
ruff check .
python -m orbit.api.app                  # http://localhost:8000
```
Rules: never present simulated data as real (labels are enforced in API and UI); never change a metric or test to make it pass without recording why in DECISIONS.md; keep SQL parameterised; any model-selection or policy change made after seeing test/backtest results must be disclosed in DECISIONS.md and the old result kept.
Use real M5 data: download it from Kaggle, then `export ORBIT_M5_DIR=/path/to/m5` and re-run the pipeline with `--reset`, training and backtest. Expect different numbers; nothing in the docs should then be quoted without regenerating it.
