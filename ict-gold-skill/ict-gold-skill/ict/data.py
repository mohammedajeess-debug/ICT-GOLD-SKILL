"""Market data: CSV in/out, resampling, and a free Dukascopy downloader (standard library only).

A bar is a tuple (ts, open, high, low, close) with ts = UTC epoch seconds of the bar start.
Everything is stored once as 1-minute BID candles; 3m / 5m / 15m are resampled locally."""
import calendar
import csv
import lzma
import os
import struct
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone


def data_path(root, symbol):
    return os.path.join(root, "data", "%s_1m.csv" % symbol)


def parse_ts(s):
    s = s.strip()
    if s.isdigit():
        v = int(s)
        return v // 1000 if v > 10 ** 11 else v
    s = s.replace("T", " ").replace(".", "-", 2).rstrip("Z")
    if "+" in s[10:]:
        s = s.split("+")[0].strip()
    y, mo, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
    hh = int(s[11:13]) if len(s) >= 13 else 0
    mi = int(s[14:16]) if len(s) >= 16 else 0
    ss = int(s[17:19]) if len(s) >= 19 else 0
    return calendar.timegm((y, mo, d, hh, mi, ss, 0, 0, 0))


def fmt_ts(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def load_csv(path):
    bars = []
    with open(path, newline="") as f:
        for row in csv.reader(f):
            if not row or not row[0][:1].isdigit():
                continue
            bars.append((parse_ts(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4])))
    bars.sort()
    return bars


def save_csv(path, bars):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_utc", "open", "high", "low", "close"])
        for ts, o, h, l, c in bars:
            w.writerow([fmt_ts(ts), repr(o), repr(h), repr(l), repr(c)])


def resample(bars, n):
    """Aggregate 1-minute bars into n-minute bars aligned to UTC multiples of n minutes."""
    step = n * 60
    out = []
    cur = None
    for ts, o, h, l, c in bars:
        b = ts - ts % step
        if cur is None or cur[0] != b:
            if cur is not None:
                out.append(tuple(cur))
            cur = [b, o, h, l, c]
        else:
            if h > cur[2]:
                cur[2] = h
            if l < cur[3]:
                cur[3] = l
            cur[4] = c
    if cur is not None:
        out.append(tuple(cur))
    return out


def import_csv(src, dst, tz_offset_hours=0.0):
    """Import a CSV exported from MT5 / TradingView / anything. Needs columns: time, open, high, low, close.
    MT5 style (date and time in separate columns, e.g. 2024.01.02,10:00,...) is detected automatically.
    If the file is NOT in UTC (MT5 server time!), pass tz_offset_hours (e.g. 2 or 3) to shift it to UTC."""
    bars = {}
    with open(src, newline="") as f:
        rd = csv.reader(f, delimiter=",")
        for row in rd:
            if len(row) < 5 or not row[0][:1].isdigit():
                continue
            if len(row) >= 6 and ":" in row[1] and len(row[1]) <= 8:
                ts = parse_ts(row[0].replace(".", "-") + " " + row[1])
                o, h, l, c = (float(x) for x in row[2:6])
            else:
                ts = parse_ts(row[0])
                o, h, l, c = (float(x) for x in row[1:5])
            ts -= int(tz_offset_hours * 3600)
            bars[ts] = (ts, o, h, l, c)
    out = sorted(bars.values())
    save_csv(dst, out)
    return len(out)


# ---------------------------------------------------------------- Dukascopy
def _hour_url(symbol, dt):
    return "https://datafeed.dukascopy.com/datafeed/%s/%04d/%02d/%02d/%02dh_ticks.bi5" % (
        symbol, dt.year, dt.month - 1, dt.day, dt.hour)   # month is 0-based on Dukascopy


def _download_hour(symbol, dt, cache_dir, retries=4):
    p = os.path.join(cache_dir, symbol, "%04d" % dt.year, "%02d" % dt.month, "%02d" % dt.day, "%02dh.bi5" % dt.hour)
    if os.path.exists(p):
        with open(p, "rb") as f:
            return f.read()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    data = b""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(_hour_url(symbol, dt), headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:          # no data for that hour (weekend / holiday)
                data = b""
                break
            time.sleep(2 * (attempt + 1))
        except Exception:
            time.sleep(2 * (attempt + 1))
    else:
        return None                    # failed: do not cache, re-run later to fill the gap
    with open(p, "wb") as f:
        f.write(data)
    return data


def decode_ticks(raw, hour_start_ts, divisor):
    """bi5 = LZMA; 20-byte big-endian records: ms-offset, ask, bid, ask-vol, bid-vol."""
    if not raw:
        return []
    try:
        buf = lzma.decompress(raw)
    except lzma.LZMAError:
        return []
    out = []
    for ms, ask, bid, av, bv in struct.iter_unpack(">IIIff", buf[: len(buf) - len(buf) % 20]):
        out.append((hour_start_ts + ms / 1000.0, bid / divisor, ask / divisor))
    return out


def ticks_to_m1(ticks):
    bars = {}
    for t, bid, ask in ticks:
        m = int(t) // 60 * 60
        b = bars.get(m)
        if b is None:
            bars[m] = [m, bid, bid, bid, bid]
        else:
            if bid > b[2]:
                b[2] = bid
            if bid < b[3]:
                b[3] = bid
            b[4] = bid
    return [tuple(v) for v in bars.values()]


def fetch_dukascopy(symbol, start, end, cfg, root, workers=2, log=print):
    """Download [start, end) as 1-minute BID candles into data/<SYMBOL>_1m.csv (merging with what exists).
    Raw hourly files are cached under data/cache, so re-running only fetches what failed or is new."""
    div = cfg["symbols"][symbol]["price_divisor"]
    cache = os.path.join(root, "data", "cache")
    hours = []
    t = start.replace(minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    while t < end:
        if t.weekday() != 5:           # Saturday never has data
            hours.append(t)
        t += timedelta(hours=1)
    path = data_path(root, symbol)
    merged = {}
    if os.path.exists(path):
        for b in load_csv(path):
            merged[b[0]] = b
    failed = 0
    done = 0

    def work(dt):
        raw = _download_hour(symbol, dt, cache)
        return dt, raw

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for dt, raw in ex.map(work, hours):
            done += 1
            if raw is None:
                failed += 1
            else:
                ts0 = calendar.timegm(dt.timetuple())
                for b in ticks_to_m1(decode_ticks(raw, ts0, div)):
                    merged[b[0]] = b
            if done % 200 == 0:
                log("  %d / %d hours" % (done, len(hours)))
    bars = sorted(merged.values())
    lo, hi = cfg["symbols"][symbol]["sane"]
    if bars and not (lo <= bars[-1][4] <= hi):
        log("WARNING: last close %s is outside the expected range %s-%s. Check price_divisor in config." % (bars[-1][4], lo, hi))
    save_csv(path, bars)
    log("Saved %d candles to %s (%d hours failed - re-run the same command to retry)" % (len(bars), path, failed))
    return len(bars)
