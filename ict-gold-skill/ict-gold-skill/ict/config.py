"""Settings. Defaults live here; config.json only needs the values you want to change.
Price distances are in PRICE UNITS (for XAUUSD: 1.00 = one US dollar move of gold)."""
import copy
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS = {
    "symbol": "XAUUSD",                      # default chart; change with --symbol (manual)
    "timezone": "America/New_York",          # handles UTC-4 / UTC-5 (DST) automatically
    "sessions": {                            # killzones, New York local time (same as the TFO indicator)
        "ASIA": ["20:00", "00:00"],
        "LNDN": ["02:00", "05:00"],
        "NYAM": ["09:30", "11:00"],
        "NYPM": ["13:30", "16:00"],
    },
    "trade_sessions": ["ASIA", "LNDN", "NYAM", "NYPM"],   # entries allowed only inside these
    "opens": {"NY_OPEN": "09:30", "LONDON_CLOSE": "11:00"},  # open-price lines (HH:MM NY time)
    "use_levels": {"day": True, "week": True, "month": True, "sessions": True,
                   "opens": True, "dayclose": True, "mids": True},

    "sweep_tf": 5,                           # candle size (min) used to detect sweep / run
    "confirm_tfs": [3, 5, 15],               # FVG / IFVG searched on all of these
    "confirm_window_min": 90,                # how long after a sweep we wait for the IFVG
    "fvg_lookback_min": 180,                 # FVG must have formed within this time before the sweep
    "atr_period": 14,
    "displacement_atr": 0.8,                 # IFVG candle body must be >= this x ATR, else "manipulation"

    "min_rr": 1.5,                           # nearest opposite liquidity must give at least this RR
    "fallback_rr": 2.0,                      # if no such level: fixed RR target (null = skip trade)
    "max_hold_min": 480,
    "flat_time": "16:55",                    # everything closed by this NY time
    "no_trade_windows": ["16:55-18:05"],     # rollover: spreads widen

    "risk_pct": 1.0,
    "max_daily_loss_pct": 3.0,
    "max_trades_per_day": 6,
    "start_equity": 10000.0,

    "news": {"enabled": True, "before_min": 15, "after_min": 15, "impacts": ["High"]},
    "mc_runs": 10000,
    "mc_dd_limit_pct": 20.0,

    "symbols": {
        "XAUUSD": {"price_divisor": 1000, "contract_size": 100, "min_fvg": 0.30,
                   "min_close_beyond": 0.30, "stop_buffer": 0.50, "max_stop": 15.0,
                   "spread": 0.25, "slippage": 0.05, "commission_per_lot_side": 3.5,
                   "lot_step": 0.01, "min_lot": 0.01, "sane": [300, 10000]},
        "XAGUSD": {"price_divisor": 1000, "contract_size": 5000, "min_fvg": 0.02,
                   "min_close_beyond": 0.02, "stop_buffer": 0.03, "max_stop": 0.60,
                   "spread": 0.03, "slippage": 0.005, "commission_per_lot_side": 3.5,
                   "lot_step": 0.01, "min_lot": 0.01, "sane": [5, 200]},
        "EURUSD": {"price_divisor": 100000, "contract_size": 100000, "min_fvg": 0.00020,
                   "min_close_beyond": 0.00015, "stop_buffer": 0.00020, "max_stop": 0.0030,
                   "spread": 0.00010, "slippage": 0.00002, "commission_per_lot_side": 3.5,
                   "lot_step": 0.01, "min_lot": 0.01, "sane": [0.5, 2.0]},
        "GBPUSD": {"price_divisor": 100000, "contract_size": 100000, "min_fvg": 0.00025,
                   "min_close_beyond": 0.00020, "stop_buffer": 0.00025, "max_stop": 0.0035,
                   "spread": 0.00012, "slippage": 0.00002, "commission_per_lot_side": 3.5,
                   "lot_step": 0.01, "min_lot": 0.01, "sane": [0.7, 2.5]},
    },
}


def _merge(a, b):
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            _merge(a[k], v)
        else:
            a[k] = v


def load(path=None, overrides=None):
    cfg = copy.deepcopy(DEFAULTS)
    p = path or os.path.join(ROOT, "config.json")
    if os.path.exists(p):
        with open(p) as f:
            _merge(cfg, json.load(f))
    if overrides:
        _merge(cfg, overrides)
    return for_symbol(cfg, cfg["symbol"])


def for_symbol(cfg, symbol):
    symbol = symbol.upper()
    if symbol not in cfg["symbols"]:
        raise SystemExit("Unknown symbol %s. Known: %s" % (symbol, ", ".join(cfg["symbols"])))
    cfg["symbol"] = symbol
    cfg["sym"] = cfg["symbols"][symbol]
    return cfg
