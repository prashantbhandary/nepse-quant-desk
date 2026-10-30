"""ML model: walk-forward training, backtest and live scoring."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

from .features import FEATURES

HORIZON = 10          # trading days we expect to hold
TARGET = 0.04         # "win" = +4% or more in 10 days (covers ~1.5% costs with margin)
MIN_TURNOVER = 5e6    # Rs 50 lakh average daily turnover -> tradable
MIN_PRICE = 100       # skip mutual funds / debentures-like instruments


NON_SHARE_SECTORS = ("Promoter Share", "Mutual Fund")
DEBENTURE = r"(?:D\d{2,4}|GED)$"  # bank debentures: <bank code>D<maturity year>, e.g. MBLD87


def is_share(panel: pd.DataFrame) -> pd.Series:
    """Ordinary listed shares only — no debentures/bonds, promoter shares or mutual funds."""
    sector = panel["sector"] if "sector" in panel else pd.Series("", index=panel.index)
    return ~panel["symbol"].str.contains(DEBENTURE) & ~sector.isin(NON_SHARE_SECTORS)


def tradable(panel: pd.DataFrame) -> pd.Series:
    return (
        is_share(panel)
        & (panel["turnover20"] >= MIN_TURNOVER)
        & (panel["close"] >= MIN_PRICE)
        & panel["px_sma200"].notna()
        & (panel["gap_days"] < 15)
    )


def make_label(panel: pd.DataFrame) -> pd.Series:
    return (panel["fwd10_open"] >= TARGET).astype(int)


def _clf():
    return HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.04, max_leaf_nodes=31, min_samples_leaf=200,
        l2_regularization=1.0, max_features=0.7, random_state=7,
    )


def _reg():
    return HistGradientBoostingRegressor(
        max_iter=300, learning_rate=0.04, max_leaf_nodes=31, min_samples_leaf=200,
        l2_regularization=1.0, max_features=0.7, random_state=7, loss="absolute_error",
    )


def fit(train: pd.DataFrame, feats=FEATURES):
    y = make_label(train)
    clf = _clf().fit(train[feats], y)
    # regressor on clipped forward return for expected-return estimate
    reg = _reg().fit(train[feats], train["fwd10_open"].clip(-0.3, 0.3))
    return clf, reg


def walk_forward(panel: pd.DataFrame, start_year=2016, feats=FEATURES, embargo_days=20):
    """Train on all data before each year (minus an embargo gap), predict that year."""
    data = panel[tradable(panel) & panel["fwd10_open"].notna()].copy()
    years = sorted(y for y in data["date"].dt.year.unique() if y >= start_year)
    preds = []
    for y in years:
        cut = pd.Timestamp(f"{y}-01-01")
        tr = data[data["date"] < cut - pd.Timedelta(days=embargo_days * 1.5)]
        te = data[data["date"].dt.year == y]
        if len(tr) < 5000 or te.empty:
            continue
        clf, reg = fit(tr, feats)
        te = te.copy()
        te["prob"] = clf.predict_proba(te[feats])[:, 1]
        te["exp_ret"] = reg.predict(te[feats])
        preds.append(te)
    return pd.concat(preds) if preds else pd.DataFrame()


# ---------------- costs (approx. NEPSE, individual investor) ----------------
def broker_rate(amount: float) -> float:
    if amount <= 50_000:
        return 0.0036
    if amount <= 500_000:
        return 0.0033
    if amount <= 2_000_000:
        return 0.0031
    if amount <= 10_000_000:
        return 0.0027
    return 0.0024


SEBON = 0.00015
DP_FEE = 25.0
CGT_SHORT = 0.075
CGT_LONG = 0.05


def net_trade_return(gross: float, position=100_000.0, held_days=14) -> float:
    """Return after commission (both sides), SEBON fee, DP fee and capital-gains tax."""
    buy_cost = position * (broker_rate(position) + SEBON)
    sell_val = position * (1 + gross)
    sell_cost = sell_val * (broker_rate(sell_val) + SEBON) + DP_FEE
    profit = sell_val - sell_cost - position - buy_cost
    if profit > 0:
        profit *= 1 - (CGT_LONG if held_days > 365 else CGT_SHORT)
    return profit / position


def backtest(preds: pd.DataFrame, top_n=5, rebalance=HORIZON, min_prob=0.0, regime=False,
             score="prob", position=100_000.0):
    """Every `rebalance` trading days, buy top_n by score, hold HORIZON days, then sell."""
    dates = sorted(preds["date"].unique())
    picks_days = dates[::rebalance]
    rows = []
    for d in picks_days:
        day = preds[preds["date"] == d]
        if regime and day["mkt_px_sma50"].iloc[0] < 0:
            rows.append({"date": d, "ret": 0.0, "mkt": day["fwd10_open"].mean(), "n": 0, "hit": np.nan})
            continue
        cand = day[day["prob"] >= min_prob].nlargest(top_n, score)
        if cand.empty:
            rows.append({"date": d, "ret": 0.0, "mkt": day["fwd10_open"].mean(), "n": 0, "hit": np.nan})
            continue
        nets = [net_trade_return(g, position) for g in cand["fwd10_open"]]
        rows.append({
            "date": d, "ret": float(np.mean(nets)), "gross": float(cand["fwd10_open"].mean()),
            "mkt": float(day["fwd10_open"].mean()), "n": len(cand),
            "hit": float((cand["fwd10_open"] > 0.015).mean()),
        })
    bt = pd.DataFrame(rows)
    bt["equity"] = (1 + bt["ret"]).cumprod()
    bt["mkt_equity"] = (1 + bt["mkt"]).cumprod()
    return bt


def summarize(bt: pd.DataFrame, periods_per_year=24) -> dict:
    yrs = len(bt) / periods_per_year
    eq, mk = bt["equity"].iloc[-1], bt["mkt_equity"].iloc[-1]
    dd = (bt["equity"] / bt["equity"].cummax() - 1).min()
    r = bt["ret"]
    return {
        "periods": len(bt),
        "cagr": eq ** (1 / yrs) - 1 if yrs > 0 else 0,
        "mkt_cagr": mk ** (1 / yrs) - 1 if yrs > 0 else 0,
        "max_drawdown": float(dd),
        "sharpe": float(r.mean() / r.std() * np.sqrt(periods_per_year)) if r.std() > 0 else 0,
        "win_rate": float(bt["hit"].mean()),
        "invested_share": float((bt["n"] > 0).mean()),
    }
