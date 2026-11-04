"""Liquidity levels, same idea as the 'ICT Killzones & Pivots' indicator.

A level is a tuple (name, price, kind, rank). kind: 'high' (buy-side liquidity above price),
'low' (sell-side liquidity below price) or 'mid' (midpoints, opens, day close: can react from either side).
Only COMPLETED periods are used, so there is no look-ahead.

Trading day = 17:00 New York to 17:00 New York (forex rollover). Because the timezone is
America/New_York, the UTC-4 / UTC-5 daylight-saving switch is handled automatically."""
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

RANK = {"PM": 6, "PW": 5, "PD": 4, "SESS": 3, "OPEN": 2}


def hm(s):
    a, b = s.split(":")
    return int(a) * 60 + int(b)


def in_window(t, a, b):
    """t, a, b = minutes since local midnight. b == 0 means midnight (end of day). Handles windows crossing midnight."""
    if b == 0:
        b = 1440
    return (a <= t < b) if a < b else (t >= a or t < b)


def local_ts(tz, date, minutes):
    return datetime(date.year, date.month, date.day, minutes // 60, minutes % 60, tzinfo=tz).timestamp()


def trading_day(dt):
    """Trading-day key of a local datetime: the day rolls over at 17:00 local."""
    return (dt + timedelta(hours=7)).date()


class LevelBook:
    def __init__(self, m1, cfg):
        tz = self.tz = ZoneInfo(cfg["timezone"])
        self.cfg = cfg
        wins = {n: (hm(a), hm(b)) for n, (a, b) in cfg["sessions"].items()}
        omin = {hm(v): n for n, v in cfg["opens"].items()}
        day = {}
        inst = {}
        opens = {n: ([], []) for n in cfg["opens"]}
        dclose = ([], [])
        for ts, o, h, l, c in m1:
            dt = datetime.fromtimestamp(ts, tz)
            k = (dt + timedelta(hours=7)).date()
            d = day.get(k)
            if d is None:
                day[k] = [h, l]
            else:
                if h > d[0]:
                    d[0] = h
                if l < d[1]:
                    d[1] = l
            t = dt.hour * 60 + dt.minute
            for n, (a, b) in wins.items():
                if in_window(t, a, b):
                    sd = dt.date() if (a < b or b == 0 or t >= a) else dt.date() - timedelta(days=1)
                    x = inst.get((n, sd))
                    if x is None:
                        if b == 0:
                            end = local_ts(tz, sd + timedelta(days=1), 0)
                        elif a < b:
                            end = local_ts(tz, sd, b)
                        else:
                            end = local_ts(tz, sd + timedelta(days=1), b)
                        inst[(n, sd)] = [h, l, end]
                    else:
                        if h > x[0]:
                            x[0] = h
                        if l < x[1]:
                            x[1] = l
            if t in omin:
                opens[omin[t]][0].append(ts)
                opens[omin[t]][1].append(o)
            if t == 16 * 60 + 59:
                dclose[0].append(ts + 60)
                dclose[1].append(c)
        self.day = day
        self.dkeys = sorted(day)
        wk, mo = {}, {}
        for k, (h, l) in day.items():
            for store, key in ((wk, k.isocalendar()[:2]), (mo, (k.year, k.month))):
                x = store.get(key)
                if x is None:
                    store[key] = [h, l]
                else:
                    x[0] = max(x[0], h)
                    x[1] = min(x[1], l)
        self.wk, self.wkeys = wk, sorted(wk)
        self.mo, self.mkeys = mo, sorted(mo)
        self.sess = {n: sorted((e[2], e[0], e[1]) for (nn, _), e in inst.items() if nn == n) for n in wins}
        self.sess_end = {n: [x[0] for x in v] for n, v in self.sess.items()}
        self.opens = opens
        self.dclose = dclose
        self.cache = {}

    def _htf(self, k):
        r = self.cache.get(k)
        if r is not None:
            return r
        u = self.cfg["use_levels"]
        r = []

        def add(tag, data):
            h, l = data
            r.append((tag + "H", h, "high", RANK[tag[:2]]))
            r.append((tag + "L", l, "low", RANK[tag[:2]]))
            if u["mids"]:
                r.append((tag + "M", (h + l) / 2.0, "mid", RANK[tag[:2]]))

        i = bisect_left(self.dkeys, k) - 1
        if u["day"] and i >= 0:
            add("PD", self.day[self.dkeys[i]])
        w = k.isocalendar()[:2]
        j = bisect_left(self.wkeys, w) - 1
        if u["week"] and j >= 0:
            add("PW", self.wk[self.wkeys[j]])
        m = (k.year, k.month)
        j = bisect_left(self.mkeys, m) - 1
        if u["month"] and j >= 0:
            add("PM", self.mo[self.mkeys[j]])
        self.cache[k] = r
        return r

    def at(self, ts):
        """All levels known at time ts (uses completed periods only)."""
        dt = datetime.fromtimestamp(ts, self.tz)
        out = list(self._htf((dt + timedelta(hours=7)).date()))
        u = self.cfg["use_levels"]
        if u["sessions"]:
            for n, ends in self.sess_end.items():
                i = bisect_right(ends, ts) - 1
                if i >= 0:
                    _, h, l = self.sess[n][i]
                    out.append((n + "_H", h, "high", RANK["SESS"]))
                    out.append((n + "_L", l, "low", RANK["SESS"]))
        if u["opens"]:
            for n, (tsl, pl) in self.opens.items():
                i = bisect_right(tsl, ts) - 1
                if i >= 0:
                    out.append((n, pl[i], "mid", RANK["OPEN"]))
        if u["dayclose"]:
            i = bisect_right(self.dclose[0], ts) - 1
            if i >= 0:
                out.append(("DAYCLOSE", self.dclose[1][i], "mid", RANK["OPEN"]))
        return out


def level_key(L):
    return (L[0], round(L[1], 6))
