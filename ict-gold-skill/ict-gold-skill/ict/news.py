"""Economic-calendar news filter (a FILTER, not a direction predictor).

Free source: Forex Factory weekly JSON feed (unofficial, current week only, no 'actual' values).
Run `python run.py news` regularly (e.g. every Sunday) and the events are appended to
data/news_history.csv, which slowly becomes your own history for backtests."""
import csv
import json
import os
import urllib.request
from bisect import bisect_left, bisect_right
from datetime import datetime

FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CURRENCIES = {"XAUUSD": {"USD"}, "XAGUSD": {"USD"}, "EURUSD": {"EUR", "USD"}, "GBPUSD": {"GBP", "USD"}}


def history_path(root):
    return os.path.join(root, "data", "news_history.csv")


def fetch_weekly(timeout=20):
    req = urllib.request.Request(FEED, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def merge_history(events, path):
    rows = {}
    if os.path.exists(path):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                rows[(r["epoch"], r["currency"], r["title"])] = r
    added = 0
    for e in events:
        try:
            ep = str(int(datetime.fromisoformat(e["date"]).timestamp()))
        except Exception:
            continue
        key = (ep, e.get("country", ""), e.get("title", ""))
        if key not in rows:
            rows[key] = {"epoch": ep, "currency": e.get("country", ""), "impact": e.get("impact", ""),
                         "title": e.get("title", ""), "forecast": e.get("forecast", ""), "previous": e.get("previous", "")}
            added += 1
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["epoch", "currency", "impact", "title", "forecast", "previous"])
        w.writeheader()
        for r in sorted(rows.values(), key=lambda r: int(r["epoch"])):
            w.writerow(r)
    return added, len(rows)


class NewsFilter:
    def __init__(self, events, before_min=15, after_min=15, impacts=("High",)):
        """events: list of (epoch, currency, impact, title)."""
        self.ev = sorted(events)
        self.t = [e[0] for e in self.ev]
        self.before = before_min * 60
        self.after = after_min * 60
        self.impacts = set(impacts)

    @classmethod
    def from_csv(cls, path, **kw):
        ev = []
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                ev.append((int(r["epoch"]), r["currency"], r["impact"], r["title"]))
        return cls(ev, **kw)

    def blocked(self, ts, currencies):
        """Title of a blocking event if ts is inside a blackout window, else None."""
        lo = bisect_left(self.t, ts - self.after)
        hi = bisect_right(self.t, ts + self.before)
        for e in self.ev[lo:hi]:
            if e[1] in currencies and e[2] in self.impacts:
                return "%s %s" % (e[1], e[3])
        return None
