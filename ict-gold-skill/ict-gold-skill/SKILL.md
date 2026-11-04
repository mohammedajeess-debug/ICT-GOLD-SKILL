---
name: ict-gold
description: Backtest and flag ICT-style liquidity-sweep + IFVG setups on XAUUSD (default) and XAGUSD, EURUSD, GBPUSD across Asia/London/New York sessions, with news blackout filter, risk rules, journal and Monte Carlo report. Research/paper-trading tool, not financial advice.
---

Run from the repo folder (Python 3.9+, no pip packages needed):

- `python run.py fetch --from 2024-01-01 --to 2024-06-01`  free Dukascopy 1-minute data (slow; run once)
- `python run.py import-csv export.csv --tz-offset-hours 3`  or import an MT5/TradingView CSV
- `python run.py news`  save this week's Forex Factory calendar (run weekly to build history)
- `python run.py backtest --from 2024-01-01 --split 0.6`  replay, report, journal in out/
- `python run.py signals --days 3`  what the rules flagged recently

Default symbol is XAUUSD; switch manually with `--symbol XAGUSD`. All settings: ict/config.py (override in config.json).
Always read the TEST line of the report, never only the train line. Paper/demo trade first.
