# ict-gold-skill

ICT liquidity-sweep + IFVG toolkit. **Default symbol XAUUSD** (switch manually: `--symbol XAGUSD|EURUSD|GBPUSD`).
Pure Python standard library (3.9+). Research / paper trading only. Not financial advice.

## Strategy (what the code really does)
1. **Levels** (same family as the "ICT Killzones & Pivots" indicator, New York time, DST handled by `America/New_York`):
   previous session high/low (ASIA 20:00-00:00, LNDN 02:00-05:00, NYAM 09:30-11:00, NYPM 13:30-16:00),
   previous day / week / month high, low, mid (trading day = 17:00 to 17:00 NY), NY open (09:30),
   London close (11:00), previous-day close.
2. **Sweep vs run** on 5m candles: wick through a level and close back = sweep; close beyond = run (level consumed, no trade).
3. **Confirmation**: FVG (3m, 5m, 15m) formed before the sweep; a strong displacement candle closing through it = IFVG.
   Weak close (body < 0.8 x ATR or barely beyond the gap) = manipulation candle, ignored.
4. **Entry** next 1m open. **Stop** beyond the sweep extreme + buffer. **Target** nearest opposite liquidity with RR >= 1.5, else fixed 2R.
5. **Gates**: session window, rollover window 16:55-18:05, news blackout (+-15 min, High impact, USD for gold),
   max 6 trades/day, 3% daily loss limit, lot size from risk % and stop distance.
6. **Backtest**: real candles, no look-ahead, spread + slippage + commission, same-candle stop/target = stop,
   train/test split, per session / level / timeframe breakdown, 10,000-run Monte Carlo on your real trade results,
   and `out/journal_<SYMBOL>.csv` listing every setup (traded or skipped, and why).

## Quick start
```
python3 -m unittest discover -s tests -v          # offline self-test
python3 run.py fetch --from 2024-01-01 --to 2024-04-01     # free data, slow (cached), run once
python3 run.py news                                # weekly
python3 run.py backtest --from 2024-01-01 --split 0.6
```
Faster alternative: export 1-minute candles from MT5/TradingView and `python3 run.py import-csv file.csv --tz-offset-hours 3`
(MT5 server time is usually UTC+2/+3 - the file must end up in UTC).

## Honest limits
- The Dukascopy downloader and the news fetcher were written without internet access in the build sandbox, so they are
  **untested against the live servers**. The strategy engine is tested (offline unit tests + random-walk run).
  After the first fetch check the warning about price range; if prices look 10x/100x off, fix `price_divisor` in `ict/config.py`.
- ICT terms are subjective; every threshold (displacement, min FVG size, buffers) is a config value. Tuning many numbers
  on one history overfits: judge only the TEST part.
- Forex Factory's free feed has no history and no actual values; history builds up only from the day you start `news`.
- Spread is a fixed number, real spreads widen at news/rollover. Dukascopy data is bid-only.
- No live order execution and no live data stream (by design: alerts/paper first).
