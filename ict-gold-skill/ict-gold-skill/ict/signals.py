"""Candle helpers: ATR and Fair Value Gaps.  Candle = (start, open, high, low, close)."""


def atr_series(C, n=14):
    """Simple-average ATR using only candles up to and including index i (no look-ahead)."""
    tr = []
    prev = None
    for _, o, h, l, c in C:
        tr.append(h - l if prev is None else max(h - l, abs(h - prev), abs(l - prev)))
        prev = c
    out = []
    run = 0.0
    for i, v in enumerate(tr):
        run += v
        if i >= n:
            run -= tr[i - n]
        out.append(run / min(i + 1, n))
    return out


def find_fvgs(C, min_size, step):
    """3-candle gaps. Returns list of (idx_of_3rd_candle, kind, bottom, top).
    bull: candle1.high < candle3.low  -> gap between them.
    bear: candle1.low  > candle3.high -> gap between them."""
    out = []
    for i in range(2, len(C)):
        a, c = C[i - 2], C[i]
        if c[0] - a[0] != 2 * step:        # data gap (weekend etc.)
            continue
        if c[3] - a[2] >= min_size:
            out.append((i, "bull", a[2], c[3]))
        elif a[3] - c[2] >= min_size:
            out.append((i, "bear", c[2], a[3]))
    return out
