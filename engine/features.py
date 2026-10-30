"""Data cleaning (bonus/rights adjustment) and feature engineering for NEPSE stocks."""
from __future__ import annotations

import numpy as np
import pandas as pd

LIMIT_15_FROM = pd.Timestamp("2026-04-20")  # daily price limit 10% -> 15% (NEPSE, Baisakh 7, 2083)
COLS = ["published_date", "open", "high", "low", "close", "per_change", "traded_quantity", "traded_amount"]


def load_symbol_csv(path) -> pd.DataFrame:
    d = pd.read_csv(path)
    d = d[[c for c in COLS if c in d.columns]].copy()
    d["date"] = pd.to_datetime(d["published_date"], errors="coerce")
    d = d.dropna(subset=["date", "close"]).drop_duplicates("date", keep="last").sort_values("date")
    for c in ["open", "high", "low", "close", "per_change", "traded_quantity", "traded_amount"]:
        if c in d:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d[d["close"] > 0]
    # repair bad open/high/low
    for c in ["open", "high", "low"]:
        d[c] = d[c].where(d[c] > 0, d["close"])
    d["high"] = d[["high", "open", "close"]].max(axis=1)
    d["low"] = d[["low", "open", "close"]].min(axis=1)
    return d.reset_index(drop=True)


def adjust_corporate_actions(d: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Back-adjust prices for bonus shares / rights.

    NEPSE moves at most ~10% a day. A bigger overnight drop is a book-closure
    adjustment. From ~2019 the source's per_change is computed against the
    *adjusted* previous close, so the gap between raw change and per_change
    gives the exact adjustment factor.
    """
    d = d.copy()
    prev = d["close"].shift(1)
    raw = d["close"] / prev - 1
    pc = d["per_change"] / 100
    implied_prev = d["close"] / (1 + pc)
    factor = pd.Series(1.0, index=d.index)
    # case A: per_change disagrees with raw change -> exact factor
    a = pc.notna() & ((raw - pc) < -0.03) & (raw < -0.02) & (implied_prev > 0)
    factor[a] = (implied_prev / prev)[a]
    # case B: raw drop beyond the daily limit and per_change agrees -> assume flat day.
    # NEPSE raised the limit from 10% to 15% on 2026-04-20; after that a per_change that agrees
    # with a 10-15% drop is a real move, not a book closure.
    new_limit = d["date"] >= LIMIT_15_FROM
    b = (~a) & (raw < -0.11) & ~(new_limit & pc.notna()) & ~(new_limit & (raw >= -0.16))
    factor[b] = (d["close"] / prev)[b]
    # only accept downward adjustments that look like corporate actions
    factor = factor.where((factor < 0.97) & (factor > 0.2), 1.0)
    events = [
        {"date": d.loc[i, "date"].strftime("%Y-%m-%d"), "factor": round(float(factor[i]), 4)}
        for i in factor.index[factor < 1.0]
    ]
    # cumulative factor applied to all rows BEFORE the event
    cum = factor[::-1].cumprod()[::-1].shift(-1).fillna(1.0)
    for c in ["open", "high", "low", "close"]:
        d["adj_" + c] = d[c] * cum
    d["adj_volume"] = d["traded_quantity"] / cum
    return d, events


def rsi(s: pd.Series, n=14):
    delta = s.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def stock_features(d: pd.DataFrame) -> pd.DataFrame:
    c, h, l = d["adj_close"], d["adj_high"], d["adj_low"]
    v = d["traded_quantity"].fillna(0)
    amt = d["traded_amount"].fillna(0)
    f = pd.DataFrame(index=d.index)
    f["date"] = d["date"]
    f["close"] = d["close"]
    f["adj_close"] = c
    f["ret1"] = c.pct_change().clip(-0.2, 0.2)
    for n in [5, 10, 20, 60, 120]:
        f[f"ret{n}"] = c.pct_change(n)
    for n in [10, 20, 50, 200]:
        f[f"sma{n}"] = c.rolling(n, min_periods=int(n * 0.8)).mean()
        f[f"px_sma{n}"] = c / f[f"sma{n}"] - 1
    f["sma20_slope"] = f["sma20"].pct_change(5)
    f["sma50_slope"] = f["sma50"].pct_change(10)
    f["rsi14"] = rsi(c, 14)
    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    sig = macd.ewm(span=9, adjust=False).mean()
    f["macd_hist"] = (macd - sig) / c
    f["macd"] = macd / c
    std20 = c.rolling(20).std()
    f["bb_pctb"] = (c - (f["sma20"] - 2 * std20)) / (4 * std20)
    f["bb_width"] = 4 * std20 / f["sma20"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    f["atr14"] = tr.rolling(14).mean()
    f["atr_pct"] = f["atr14"] / c
    f["vol20"] = f["ret1"].rolling(20).std()
    f["vol_ratio"] = v / v.rolling(20).mean().replace(0, np.nan)
    f["vol_ratio5"] = v.rolling(5).mean() / v.rolling(60).mean().replace(0, np.nan)
    f["turnover20"] = amt.rolling(20, min_periods=10).mean()
    f["log_turnover"] = np.log1p(f["turnover20"])
    hi252, lo252 = h.rolling(252, min_periods=60).max(), l.rolling(252, min_periods=60).min()
    f["dist_hi52"] = c / hi252 - 1
    f["dist_lo52"] = c / lo252 - 1
    f["hi20_break"] = (c >= h.rolling(20).max().shift(1)).astype(float)
    f["up_days10"] = (f["ret1"] > 0).rolling(10).mean()
    f["gap_days"] = d["date"].diff().dt.days.fillna(1)
    # forward returns (labels) — measured on trading rows of this stock
    for n in [5, 10, 20]:
        f[f"fwd{n}"] = c.shift(-n) / c - 1
    # realistic swing-trade label: entry next day's open, exit close 10 days later
    f["fwd10_open"] = c.shift(-10) / d["adj_open"].shift(-1) - 1
    f["fwd20_open"] = c.shift(-20) / d["adj_open"].shift(-1) - 1
    # max adverse excursion over next 10 days (for stop-loss realism)
    f["fwd10_min"] = l[::-1].rolling(10, min_periods=1).min()[::-1].shift(-1) / c - 1
    return f


FEATURES = [
    "ret5", "ret10", "ret20", "ret60", "ret120",
    "px_sma10", "px_sma20", "px_sma50", "px_sma200", "sma20_slope", "sma50_slope",
    "rsi14", "macd_hist", "macd", "bb_pctb", "bb_width", "atr_pct", "vol20",
    "vol_ratio", "vol_ratio5", "log_turnover", "dist_hi52", "dist_lo52", "hi20_break", "up_days10",
    # market + cross-sectional
    "mkt_ret5", "mkt_ret20", "mkt_px_sma50", "breadth50", "breadth200", "rs20", "rs60",
    "rank_ret20", "rank_ret60", "rank_vol_ratio", "sector_ret20", "sector_rs20",
]


def add_market_features(panel: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Panel = concat of stock_features with 'symbol' and 'sector'. Adds market-wide features."""
    liquid = panel["turnover20"] > 2e6
    g = panel[liquid].groupby("date")
    mkt = pd.DataFrame({
        "mkt_r1": g["ret1"].mean(),
        "breadth50": g["px_sma50"].apply(lambda x: (x > 0).mean()),
        "breadth200": g["px_sma200"].apply(lambda x: (x > 0).mean()),
        "n": g.size(),
        "adv": g["ret1"].apply(lambda x: (x > 0).sum()),
        "dec": g["ret1"].apply(lambda x: (x < 0).sum()),
    }).sort_index()
    mkt = mkt[mkt["n"] >= 10]
    mkt["mkt_index"] = (1 + mkt["mkt_r1"].fillna(0)).cumprod() * 1000
    mkt["mkt_ret5"] = mkt["mkt_index"].pct_change(5)
    mkt["mkt_ret20"] = mkt["mkt_index"].pct_change(20)
    mkt["mkt_sma50"] = mkt["mkt_index"].rolling(50).mean()
    mkt["mkt_px_sma50"] = mkt["mkt_index"] / mkt["mkt_sma50"] - 1
    mkt["mkt_ret60"] = mkt["mkt_index"].pct_change(60)
    panel = panel.merge(mkt[["mkt_ret5", "mkt_ret20", "mkt_ret60", "mkt_px_sma50", "breadth50", "breadth200"]],
                        left_on="date", right_index=True, how="left")
    panel["rs20"] = panel["ret20"] - panel["mkt_ret20"]
    panel["rs60"] = panel["ret60"] - panel["mkt_ret60"]
    gd = panel.groupby("date")
    panel["rank_ret20"] = gd["ret20"].rank(pct=True)
    panel["rank_ret60"] = gd["ret60"].rank(pct=True)
    panel["rank_vol_ratio"] = gd["vol_ratio"].rank(pct=True)
    sec = panel.groupby(["date", "sector"])["ret20"].transform("median")
    panel["sector_ret20"] = sec
    panel["sector_rs20"] = panel["ret20"] - sec
    return panel, mkt
