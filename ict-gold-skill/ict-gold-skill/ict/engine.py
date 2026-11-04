"""Strategy engine (replay of real candles, no look-ahead).

Flow per 5-minute candle:
 1. SWEEP : candle opens on one side of a liquidity level, wicks through it, CLOSES back = sweep.
            candle closes beyond the level = RUN (level is simply consumed, no trade).
 2. CONFIRM: within the window, on 3m / 5m / 15m, find the FVG formed before the sweep (a bullish FVG for a
            high-sweep) and wait for a candle to CLOSE through it = IFVG (inversion).
            The inverting candle must be a strong displacement candle (body >= displacement_atr x ATR and
            closes clearly beyond the gap); otherwise it is logged as a 'manipulation' candle and ignored.
 3. ENTRY : next 1-minute open after the confirming candle closes.
            STOP = beyond the sweep extreme + buffer.  TARGET = nearest opposite liquidity level that gives
            at least min_rr, else fixed fallback_rr.
 4. Gates : session window, rollover window, news blackout, max trades/day, daily loss limit, lot size.
 Same-candle stop & target -> stop wins (conservative)."""
import math
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .data import resample
from .levels import hm, in_window, level_key, local_ts, trading_day
from .news import CURRENCIES
from .signals import atr_series, find_fvgs


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")


class Engine:
    def __init__(self, m1, cfg, book, news=None):
        self.m1, self.cfg, self.sym, self.book, self.news = m1, cfg, cfg["sym"], book, news
        self.ts1 = [b[0] for b in m1]
        self.tz = ZoneInfo(cfg["timezone"])
        tfs = sorted(set(cfg["confirm_tfs"] + [cfg["sweep_tf"]]))
        self.C = {tf: resample(m1, tf) for tf in tfs}
        self.S = {tf: [c[0] for c in self.C[tf]] for tf in tfs}
        self.F = {tf: find_fvgs(self.C[tf], self.sym["min_fvg"], tf * 60) for tf in tfs}
        self.Fi = {tf: [f[0] for f in self.F[tf]] for tf in tfs}
        self.A = {tf: atr_series(self.C[tf], cfg["atr_period"]) for tf in tfs}
        self.wins = {n: (hm(a), hm(b)) for n, (a, b) in cfg["sessions"].items()}
        self.nt = []
        for w in cfg["no_trade_windows"]:
            a, b = w.split("-")
            self.nt.append((hm(a), hm(b)))
        self.cur = CURRENCIES.get(cfg["symbol"], {"USD"})

    # ------------------------------------------------------------ helpers
    def session_at(self, t):
        for n, (a, b) in self.wins.items():
            if in_window(t, a, b):
                return n
        return None

    def detect(self, c, lv, consumed):
        """Sweep / run detection on one candle. Consumes every level the candle crossed."""
        _, o, h, l, cl = c
        up, dn = [], []
        for L in lv:
            key = level_key(L)
            if key in consumed:
                continue
            p = L[1]
            if L[2] != "low" and o <= p < h:
                consumed.add(key)
                if cl < p:
                    up.append(L)
            elif L[2] != "high" and l < p <= o:
                consumed.add(key)
                if cl > p:
                    dn.append(L)
        if (up and dn) or not (up or dn):
            return None
        if up:
            return {"dir": "short", "levels": up, "extreme": h}
        return {"dir": "long", "levels": dn, "extreme": l}

    def ifvg(self, tf, j, d, t0):
        """Does candle j (on timeframe tf) invert a pre-sweep FVG?  -> 'strong' | 'weak' | None"""
        C, F, fi = self.C[tf], self.F[tf], self.Fi[tf]
        c = C[j]
        lo_idx = bisect_left(self.S[tf], t0 - self.cfg["fvg_lookback_min"] * 60)
        a, b = bisect_left(fi, lo_idx), bisect_left(fi, j)
        need = "bull" if d == "short" else "bear"
        atr, k, mb = self.A[tf][j], self.cfg["displacement_atr"], self.sym["min_close_beyond"]
        res = None
        for q in range(b - 1, a - 1, -1):
            idx, kind, bot, top = F[q]
            if kind != need:
                continue
            if d == "short":
                if any(C[m][4] < bot for m in range(idx + 1, j)):
                    continue                              # already inverted earlier
                if c[4] < bot:
                    if (c[1] - c[4]) >= k * atr and (bot - c[4]) >= mb:
                        return "strong"
                    res = "weak"
            else:
                if any(C[m][4] > top for m in range(idx + 1, j)):
                    continue
                if c[4] > top:
                    if (c[4] - c[1]) >= k * atr and (c[4] - top) >= mb:
                        return "strong"
                    res = "weak"
        return res

    def find_confirmation(self, setup):
        cfg, d, t0 = self.cfg, setup["dir"], setup["t"]
        tmax = t0 + cfg["confirm_window_min"] * 60
        ev = []
        for tf in cfg["confirm_tfs"]:
            C, st = self.C[tf], self.S[tf]
            i = bisect_right(st, t0 - tf * 60)
            while i < len(C) and C[i][0] + tf * 60 <= tmax:
                ev.append((C[i][0] + tf * 60, tf, i))
                i += 1
        ev.sort()
        weak = 0
        for tc, tf, j in ev:
            c = self.C[tf][j]
            if (d == "short" and c[4] > setup["extreme"]) or (d == "long" and c[4] < setup["extreme"]):
                return None, "invalidated_run", weak
            r = self.ifvg(tf, j, d, t0)
            if r == "strong":
                return {"tf": tf, "tclose": tc}, "ok", weak
            if r == "weak":
                weak += 1
        return None, ("manipulation_only" if weak else "no_ifvg"), weak

    def simulate(self, idx, d, entry, stop, target):
        sy, cfg = self.sym, self.cfg
        sp, sl = sy["spread"], sy["slippage"]
        ts0 = self.m1[idx][0]
        dt = datetime.fromtimestamp(ts0, self.tz)
        flat = hm(cfg["flat_time"])
        t = dt.hour * 60 + dt.minute
        flat_ts = local_ts(self.tz, dt.date() if t < flat else dt.date() + timedelta(days=1), flat)
        end_ts = min(ts0 + cfg["max_hold_min"] * 60, flat_ts)
        n = len(self.m1)
        k = idx
        while k < n:
            ts, o, h, l, c = self.m1[k]
            if d == "short":                      # we buy back at the ASK = bid + spread
                if h + sp >= stop:
                    return ts, stop + sl, "stop"
                if l + sp <= target:
                    return ts, target, "target"
            else:
                if l <= stop:
                    return ts, stop - sl, "stop"
                if h >= target:
                    return ts, target, "target"
            if ts + 60 >= end_ts or k == n - 1:
                return ts, (c + sp if d == "short" else c), "time"
            k += 1
        return self.m1[-1][0], self.m1[-1][4], "time"

    # ------------------------------------------------------------ trade building
    def try_trade(self, setup, conf, consumed, st):
        cfg, sy, d = self.cfg, self.sym, setup["dir"]
        tc = conf["tclose"]
        dt = datetime.fromtimestamp(tc, self.tz)
        t = dt.hour * 60 + dt.minute
        day = trading_day(dt)
        sess = self.session_at(t)
        if sess is None or sess not in cfg["trade_sessions"]:
            return None, "outside_session", {}
        for a, b in self.nt:
            if in_window(t, a, b):
                return None, "rollover_window", {}
        if self.news is not None:
            ev = self.news.blocked(tc, self.cur)
            if ev:
                return None, "news: " + ev, {}
        if st["n"].get(day, 0) >= cfg["max_trades_per_day"]:
            return None, "max_trades_per_day", {}
        if st["R"].get(day, 0.0) * cfg["risk_pct"] <= -cfg["max_daily_loss_pct"]:
            return None, "daily_loss_limit", {}
        idx = bisect_left(self.ts1, tc)
        if idx >= len(self.m1) or self.m1[idx][0] - tc > 300:
            return None, "no_data_at_entry", {}
        e0, sp, sl = self.m1[idx][1], sy["spread"], sy["slippage"]
        if d == "short":
            entry = e0 - sl
            stop = setup["extreme"] + sy["stop_buffer"]
            risk = stop - entry
        else:
            entry = e0 + sp + sl
            stop = setup["extreme"] - sy["stop_buffer"]
            risk = entry - stop
        if risk <= 2 * sp:
            return None, "stop_too_tight", {}
        if risk > sy["max_stop"]:
            return None, "stop_too_wide", {}
        lv = [L for L in self.book.at(tc) if level_key(L) not in consumed]
        if d == "short":
            cand = [L[1] for L in lv if L[2] != "high" and entry - L[1] >= cfg["min_rr"] * risk]
            tgt = max(cand) if cand else None
        else:
            cand = [L[1] for L in lv if L[2] != "low" and L[1] - entry >= cfg["min_rr"] * risk]
            tgt = min(cand) if cand else None
        mode = "level"
        if tgt is None:
            fr = cfg["fallback_rr"]
            if not fr:
                return None, "no_target", {}
            tgt = entry - fr * risk if d == "short" else entry + fr * risk
            mode = "rr"
        risk_usd = st["eq"] * cfg["risk_pct"] / 100.0
        step = sy["lot_step"]
        lots = math.floor(risk_usd / (risk * sy["contract_size"]) / step + 1e-9) * step
        if lots < sy["min_lot"]:
            return None, "lot_too_small", {}
        ts_x, px, why = self.simulate(idx, d, entry, stop, tgt)
        sign = 1 if d == "long" else -1
        gross = (px - entry) * sign * sy["contract_size"] * lots
        pnl = gross - sy["commission_per_lot_side"] * lots * 2
        R = pnl / (risk * sy["contract_size"] * lots)
        st["eq"] += pnl
        st["n"][day] = st["n"].get(day, 0) + 1
        st["R"][day] = st["R"].get(day, 0.0) + R
        tr = {"entry_ts": self.m1[idx][0], "exit_ts": ts_x, "dir": d, "session": sess, "tf": conf["tf"],
              "level": setup["primary"], "entry": entry, "stop": stop, "target": tgt, "target_mode": mode,
              "exit": px, "exit_reason": why, "lots": lots, "R": R, "pnl": pnl, "equity": st["eq"]}
        return tr, "traded", tr

    # ------------------------------------------------------------ main loop
    def run(self, start=None, end=None):
        cfg = self.cfg
        tf = cfg["sweep_tf"]
        consumed = set()
        trades, journal = [], []
        st = {"eq": cfg["start_equity"], "n": {}, "R": {}}
        blocked = 0
        for c in self.C[tf]:
            if start and c[0] < start:
                continue
            if end and c[0] >= end:
                break
            tclose = c[0] + tf * 60
            ev = self.detect(c, self.book.at(c[0]), consumed)
            if ev is None or tclose <= blocked:
                continue
            best = max(ev["levels"], key=lambda L: (L[3], L[1] if ev["dir"] == "short" else -L[1]))
            setup = dict(ev, t=tclose, primary=best[0])
            row = {"sweep_utc": iso(tclose), "dir": ev["dir"], "level": best[0],
                   "all_levels": "|".join(L[0] for L in ev["levels"]), "extreme": ev["extreme"]}
            conf, why, weak = self.find_confirmation(setup)
            row["manipulation_candles"] = weak
            if conf is None:
                row.update(status="skipped", reason=why)
                journal.append(row)
                continue
            tr, reason, _ = self.try_trade(setup, conf, consumed, st)
            row["confirm_tf"] = conf["tf"]
            row["confirm_utc"] = iso(conf["tclose"])
            if tr is None:
                row.update(status="skipped", reason=reason)
            else:
                trades.append(tr)
                blocked = tr["exit_ts"]
                row.update(status="traded", reason=tr["exit_reason"], entry=round(tr["entry"], 5),
                           stop=round(tr["stop"], 5), target=round(tr["target"], 5),
                           exit=round(tr["exit"], 5), R=round(tr["R"], 3), session=tr["session"])
            journal.append(row)
        return trades, journal
