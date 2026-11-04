#!/usr/bin/env python3
"""ICT gold toolkit.  Default symbol: XAUUSD (switch manually with --symbol).

  python run.py fetch    --from 2024-01-01 --to 2024-06-01      # free Dukascopy 1m data (slow, run once)
  python run.py import-csv my_export.csv --tz-offset-hours 3     # or import MT5 / TradingView export
  python run.py news                                              # save this week's Forex Factory calendar
  python run.py backtest --from 2024-01-01 --split 0.6            # replay + report + journal
  python run.py signals  --days 3                                 # what the rules flagged recently
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from ict import config as C
from ict import data, news, report
from ict.engine import Engine
from ict.levels import LevelBook

ROOT = os.path.dirname(os.path.abspath(__file__))


def day(s):
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def build(args, need_data=True):
    cfg = C.load()
    if args.symbol:
        C.for_symbol(cfg, args.symbol)
    if getattr(args, "sessions", None):
        cfg["trade_sessions"] = [s.strip().upper() for s in args.sessions.split(",")]
    if getattr(args, "risk", None):
        cfg["risk_pct"] = args.risk
    return cfg


def load_engine(cfg):
    path = data.data_path(ROOT, cfg["symbol"])
    if not os.path.exists(path):
        sys.exit("No data for %s. Run `python run.py fetch ...` or `import-csv` first." % cfg["symbol"])
    t0 = time.time()
    m1 = data.load_csv(path)
    print("Loaded %d one-minute candles for %s (%s -> %s)" % (
        len(m1), cfg["symbol"], data.fmt_ts(m1[0][0]), data.fmt_ts(m1[-1][0])))
    nf = None
    npth = news.history_path(ROOT)
    if cfg["news"]["enabled"] and os.path.exists(npth):
        nf = news.NewsFilter.from_csv(npth, before_min=cfg["news"]["before_min"],
                                      after_min=cfg["news"]["after_min"], impacts=cfg["news"]["impacts"])
        print("News filter ON (%d events in data/news_history.csv)" % len(nf.ev))
    else:
        print("News filter OFF (no data/news_history.csv yet - run `python run.py news` weekly)")
    book = LevelBook(m1, cfg)
    eng = Engine(m1, cfg, book, nf)
    print("Prepared in %.1fs" % (time.time() - t0))
    return m1, eng


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("fetch", "import-csv", "news", "backtest", "signals"):
        p = sub.add_parser(name)
        p.add_argument("--symbol", help="default XAUUSD (from config.json)")
        if name == "fetch":
            p.add_argument("--from", dest="start", required=True)
            p.add_argument("--to", dest="end", required=True)
            p.add_argument("--workers", type=int, default=2)
        if name == "import-csv":
            p.add_argument("file")
            p.add_argument("--tz-offset-hours", type=float, default=0.0,
                           help="hours the file is AHEAD of UTC (MT5 servers are often +2/+3)")
        if name in ("backtest", "signals"):
            p.add_argument("--sessions", help="e.g. NYAM,LNDN (default: all four)")
            p.add_argument("--risk", type=float, help="risk %% per trade")
        if name == "backtest":
            p.add_argument("--from", dest="start")
            p.add_argument("--to", dest="end")
            p.add_argument("--split", type=float, help="e.g. 0.6 = first 60%% train, last 40%% test")
        if name == "signals":
            p.add_argument("--days", type=int, default=3)
    args = ap.parse_args()
    cfg = build(args)

    if args.cmd == "fetch":
        data.fetch_dukascopy(cfg["symbol"], day(args.start), day(args.end), cfg, ROOT, workers=args.workers)
    elif args.cmd == "import-csv":
        n = data.import_csv(args.file, data.data_path(ROOT, cfg["symbol"]), args.tz_offset_hours)
        print("Imported %d candles -> %s" % (n, data.data_path(ROOT, cfg["symbol"])))
    elif args.cmd == "news":
        ev = news.fetch_weekly()
        added, total = news.merge_history(ev, news.history_path(ROOT))
        print("Fetched %d events, %d new, %d in history." % (len(ev), added, total))
    elif args.cmd == "backtest":
        m1, eng = load_engine(cfg)
        start = day(args.start).timestamp() if args.start else None
        end = day(args.end).timestamp() if args.end else None
        trades, journal = eng.run(start, end)
        lo, hi = start or m1[0][0], end or m1[-1][0]
        split_ts = lo + args.split * (hi - lo) if args.split else None
        report.print_report(trades, journal, cfg, split_ts)
        jp = os.path.join(ROOT, "out", "journal_%s.csv" % cfg["symbol"])
        report.write_journal(jp, journal)
        print("Journal (every setup, traded or skipped, with the reason): %s" % jp)
    elif args.cmd == "signals":
        m1, eng = load_engine(cfg)
        start = m1[-1][0] - args.days * 86400
        trades, journal = eng.run(start, None)
        print("Rule-based signals in the last %d days of data (NOT live; this is alert logic on stored candles):" % args.days)
        for r in journal:
            print("  %s  %-5s swept %-12s -> %s %s" % (r["sweep_utc"], r["dir"], r["level"], r["status"],
                                                      ("(%s)" % r["reason"]) if r["status"] == "skipped" else
                                                      "entry %s stop %s target %s" % (r["entry"], r["stop"], r["target"])))
        if not journal:
            print("  none")


if __name__ == "__main__":
    main()
