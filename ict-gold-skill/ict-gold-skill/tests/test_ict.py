import os
import sys
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ict import config as C                      # noqa: E402
from ict.data import resample                    # noqa: E402
from ict.engine import Engine                    # noqa: E402
from ict.levels import LevelBook, in_window, trading_day   # noqa: E402
from ict.news import NewsFilter                  # noqa: E402
from ict.signals import find_fvgs                # noqa: E402

NY = ZoneInfo("America/New_York")


def ts(d, h, m):
    return int(datetime(d.year, d.month, d.day, h, m, tzinfo=NY).timestamp())


def candle_to_m1(t0, o, h, l, c):
    """Five 1-minute bars that together make one 5-minute candle (o,h,l,c)."""
    pts = [o, h, l, c, c, c]
    return [(t0 + 60 * i, pts[i], max(pts[i], pts[i + 1]), min(pts[i], pts[i + 1]), pts[i + 1]) for i in range(5)]


class StubBook:
    def __init__(self, levels):
        self.levels = levels

    def at(self, t):
        return list(self.levels)


def scenario():
    d = date(2025, 3, 12)                         # after the US DST change: NY = UTC-4
    m1 = []
    t = ts(d, 7, 0)
    while t < ts(d, 9, 30):                       # flat warm-up
        m1 += candle_to_m1(t, 2000, 2000.4, 1999.6, 2000)
        t += 300
    candles = [
        (2000, 2002, 1999.8, 2001.8),             # A  (high 2002)
        (2001.8, 2007, 2001.6, 2006.8),           # B  impulse
        (2006.8, 2008.5, 2005, 2008),             # C  (low 2005) -> bullish FVG 2002-2005
        (2008, 2010.6, 2007.5, 2009.4),           # D  sweeps 2010
        (2009.4, 2009.6, 2001, 2001.2),           # E  strong bearish close below the FVG = IFVG
    ]
    for c in candles:
        m1 += candle_to_m1(t, *c)
        t += 300
    p = 2001.2
    for _ in range(40):                           # drift down through 1995 to 1980
        m1 += candle_to_m1(t, p, p + 0.3, p - 1.8, p - 1.5)
        p -= 1.5
        t += 300
    return m1, d


def cfg_for_test():
    cfg = C.load()
    cfg["news"]["enabled"] = True
    return cfg


class Tests(unittest.TestCase):
    def test_resample(self):
        bars = [(0, 1, 2, 0.5, 1.5), (60, 1.5, 3, 1, 2), (120, 2, 2.5, 1.8, 2.2), (300, 2.2, 2.3, 2.0, 2.1)]
        r = resample(bars, 5)
        self.assertEqual(r[0], (0, 1, 3, 0.5, 2.2))
        self.assertEqual(len(r), 2)

    def test_fvg(self):
        C3 = [(0, 1, 2, 1, 2), (180, 2, 4, 2, 4), (360, 4, 6, 3.5, 6)]   # c1.high 2 < c3.low 3.5
        f = find_fvgs(C3, 0.3, 180)
        self.assertEqual(f, [(2, "bull", 2, 3.5)])
        D3 = [(0, 6, 6, 5, 5), (180, 5, 5, 3, 3), (360, 3, 3.5, 2, 2)]    # c1.low 5 > c3.high 3.5
        self.assertEqual(find_fvgs(D3, 0.3, 180), [(2, "bear", 3.5, 5)])

    def test_dst_and_trading_day(self):
        # NY open 09:30 is 13:30 UTC in summer, 14:30 UTC in winter
        self.assertEqual(datetime(2025, 7, 1, 9, 30, tzinfo=NY).utcoffset().total_seconds(), -4 * 3600)
        self.assertEqual(datetime(2025, 1, 6, 9, 30, tzinfo=NY).utcoffset().total_seconds(), -5 * 3600)
        sunday_open = datetime(2025, 3, 2, 17, 0, tzinfo=NY)      # Sunday 17:00 NY -> Monday's trading day
        self.assertEqual(trading_day(sunday_open), date(2025, 3, 3))
        self.assertTrue(in_window(21 * 60, 20 * 60, 0))
        self.assertTrue(in_window(1 * 60, 22 * 60, 2 * 60))

    def test_sweep_ifvg_trade(self):
        m1, d = scenario()
        cfg = cfg_for_test()
        book = StubBook([("PDH", 2010.0, "high", 4), ("PDL", 1980.0, "low", 4), ("XL", 1995.0, "low", 4)])
        trades, journal = Engine(m1, cfg, book, None).run()
        self.assertEqual(len(trades), 1, journal)
        t = trades[0]
        self.assertEqual(t["dir"], "short")
        self.assertEqual(t["level"], "PDH")
        self.assertEqual(t["exit_reason"], "target")
        self.assertEqual(t["target_mode"], "level")
        self.assertAlmostEqual(t["target"], 1980.0)
        self.assertTrue(1.9 < t["R"] < 2.3, t["R"])
        self.assertEqual(t["session"], "NYAM")

    def test_run_is_not_a_sweep(self):
        m1, d = scenario()
        cfg = cfg_for_test()
        book = StubBook([("LOW", 2008.8, "high", 4)])      # candle D closes ABOVE 2008.8 -> run, not sweep
        trades, journal = Engine(m1, cfg, book, None).run()
        self.assertEqual(trades, [])
        self.assertEqual(journal, [])

    def test_weak_inversion_is_manipulation(self):
        m1, d = scenario()
        cfg = cfg_for_test()
        cfg["displacement_atr"] = 50.0                     # impossible displacement -> always 'weak'
        book = StubBook([("PDH", 2010.0, "high", 4), ("PDL", 1980.0, "low", 4)])
        trades, journal = Engine(m1, cfg, book, None).run()
        self.assertEqual(trades, [])
        self.assertEqual(journal[0]["reason"], "manipulation_only")

    def test_news_blackout(self):
        m1, d = scenario()
        cfg = cfg_for_test()
        book = StubBook([("PDH", 2010.0, "high", 4), ("PDL", 1980.0, "low", 4)])
        nf = NewsFilter([(ts(d, 9, 55), "USD", "High", "Test Event")], 15, 15, ["High"])
        trades, journal = Engine(m1, cfg, book, nf).run()
        self.assertEqual(trades, [])
        self.assertTrue(journal[0]["reason"].startswith("news"), journal)
        nf2 = NewsFilter([(ts(d, 9, 55), "EUR", "High", "Other currency")], 15, 15, ["High"])   # irrelevant for gold
        trades, _ = Engine(m1, cfg, book, nf2).run()
        self.assertEqual(len(trades), 1)

    def test_outside_session_blocked(self):
        m1, d = scenario()
        cfg = cfg_for_test()
        cfg["trade_sessions"] = ["LNDN"]
        book = StubBook([("PDH", 2010.0, "high", 4), ("PDL", 1980.0, "low", 4)])
        trades, journal = Engine(m1, cfg, book, None).run()
        self.assertEqual(trades, [])
        self.assertEqual(journal[0]["reason"], "outside_session")

    def test_levelbook_previous_day(self):
        d1, d2 = date(2025, 3, 11), date(2025, 3, 12)
        m1 = []
        for day, hi in ((d1, 2020.0), (d2, 2005.0)):
            t = ts(day, 9, 0)
            for i in range(120):
                m1.append((t + 60 * i, 2000, hi if i == 5 else 2001, 1999, 2000))
        cfg = cfg_for_test()
        book = LevelBook(m1, cfg)
        names = {L[0]: L[1] for L in book.at(ts(d2, 9, 30))}
        self.assertEqual(names["PDH"], 2020.0)
        self.assertEqual(names["PDL"], 1999)
        self.assertAlmostEqual(names["PDM"], (2020.0 + 1999) / 2)
        self.assertNotIn("PWH", names)                      # no previous week in this tiny sample


if __name__ == "__main__":
    unittest.main()
