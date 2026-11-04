"""Backtest statistics, breakdowns and a 10,000-run Monte Carlo on the REAL trade results."""
import csv
import os
import random
from collections import defaultdict


def stats(trades):
    R = [t["R"] for t in trades]
    n = len(R)
    if n == 0:
        return {"n": 0}
    wins = [r for r in R if r > 0]
    loss = [r for r in R if r <= 0]
    aw = sum(wins) / len(wins) if wins else 0.0
    al = sum(loss) / len(loss) if loss else 0.0
    cum = peak = dd = 0.0
    for r in R:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    pf = (sum(wins) / abs(sum(loss))) if loss and sum(loss) != 0 else float("inf")
    be = abs(al) / (aw + abs(al)) if wins and loss else float("nan")
    return {"n": n, "win_rate": len(wins) / n, "avg_win_R": aw, "avg_loss_R": al, "expectancy_R": sum(R) / n,
            "total_R": sum(R), "profit_factor": pf, "max_dd_R": dd, "break_even_wr": be,
            "end_equity": trades[-1]["equity"]}


def fmt(s):
    if s["n"] == 0:
        return "no trades"
    return ("trades %d | win %.1f%% (break-even %.1f%%) | avg win %.2fR / loss %.2fR | expectancy %+.3fR | "
            "total %+.1fR | PF %.2f | maxDD %.1fR" % (s["n"], 100 * s["win_rate"], 100 * s["break_even_wr"],
                                                       s["avg_win_R"], s["avg_loss_R"], s["expectancy_R"],
                                                       s["total_R"], s["profit_factor"], s["max_dd_R"]))


def breakdown(trades, key):
    g = defaultdict(list)
    for t in trades:
        g[t[key]].append(t)
    return {k: stats(v) for k, v in sorted(g.items(), key=lambda kv: str(kv[0]))}


def monte_carlo(R, runs, risk_pct, dd_limit_pct, seed=7):
    rnd = random.Random(seed)
    dds, streaks, finals = [], [], []
    for _ in range(runs):
        x = R[:]
        rnd.shuffle(x)
        cum = peak = dd = 0.0
        s = ms = 0
        for r in x:
            cum += r
            if cum > peak:
                peak = cum
            if peak - cum > dd:
                dd = peak - cum
            if r <= 0:
                s += 1
                ms = max(ms, s)
            else:
                s = 0
        dds.append(dd)
        streaks.append(ms)
        finals.append(cum)

    def pct(a, p):
        a = sorted(a)
        return a[min(len(a) - 1, int(p / 100.0 * len(a)))]

    return {"dd50": pct(dds, 50), "dd95": pct(dds, 95), "dd99": pct(dds, 99),
            "streak50": pct(streaks, 50), "streak95": pct(streaks, 95),
            "final5": pct(finals, 5), "final50": pct(finals, 50),
            "p_dd_limit": sum(1 for d in dds if d * risk_pct >= dd_limit_pct) / runs}


def write_journal(path, journal):
    if not journal:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cols = []
    for r in journal:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(journal)


def print_report(trades, journal, cfg, split_ts=None, out=print):
    out("=" * 78)
    out("%s | sessions %s | risk %.1f%%/trade | spread %.3f" % (
        cfg["symbol"], ",".join(cfg["trade_sessions"]), cfg["risk_pct"], cfg["sym"]["spread"]))
    skipped = defaultdict(int)
    for r in journal:
        if r["status"] == "skipped":
            skipped[r["reason"].split(":")[0]] += 1
    out("setups found: %d | traded: %d | skipped: %s" % (len(journal), len(trades), dict(skipped) or "-"))
    out("")
    if split_ts is not None:
        a = [t for t in trades if t["entry_ts"] < split_ts]
        b = [t for t in trades if t["entry_ts"] >= split_ts]
        out("TRAIN (first part) : " + fmt(stats(a)))
        out("TEST  (last part)  : " + fmt(stats(b)))
        out("   -> trust the TEST line, not the train line.")
    out("ALL                : " + fmt(stats(trades)))
    for key, title in (("session", "By session"), ("level", "By level swept"), ("tf", "By IFVG timeframe"),
                       ("dir", "By direction"), ("exit_reason", "By exit")):
        out("")
        out(title + ":")
        for k, s in breakdown(trades, key).items():
            out("  %-14s %s" % (k, fmt(s)))
    if len(trades) >= 20:
        mc = monte_carlo([t["R"] for t in trades], cfg["mc_runs"], cfg["risk_pct"], cfg["mc_dd_limit_pct"])
        out("")
        out("Monte Carlo (%d reshuffles of your real trade results):" % cfg["mc_runs"])
        out("  max drawdown  median %.1fR | 95%% worst-case %.1fR | 99%% %.1fR" % (mc["dd50"], mc["dd95"], mc["dd99"]))
        out("  losing streak median %d | 95%% worst-case %d" % (mc["streak50"], mc["streak95"]))
        out("  total result  5th percentile %+.1fR | median %+.1fR" % (mc["final5"], mc["final50"]))
        out("  chance of a %.0f%% drawdown at %.1f%% risk/trade: %.1f%%" % (
            cfg["mc_dd_limit_pct"], cfg["risk_pct"], 100 * mc["p_dd_limit"]))
    else:
        out("")
        out("Too few trades (%d) for a meaningful Monte Carlo / statistics. Use more history." % len(trades))
    out("=" * 78)
