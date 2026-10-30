"""End-to-end pipeline: prices -> features -> ML ensemble -> signals -> JSON for the web app."""
from __future__ import annotations

import glob
import json
import logging
import os
import time
from datetime import datetime

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

from .features import FEATURES, add_market_features, adjust_corporate_actions, load_symbol_csv, stock_features
from . import model as M

log = logging.getLogger("pipeline")

# Final strategy settings (chosen in research — see README "How the model was tested")
FEATS = [f for f in FEATURES if f not in ("sector_ret20", "sector_rs20")]
TOP_N = 3               # how many stocks to hold (3 beat 5 in both 2016–20 and 2021–26 walk-forward tests)
EXIT_RANK = 30          # sell when a holding drops out of the top 30
BREADTH_RISK_ON = 0.40  # >40% of liquid stocks above their 50-day average = OK to buy
REBALANCE_DAYS = 10     # re-check every ~2 weeks (the tested rhythm)
MODEL_VERSION = 4  # 4: shares only, 15% price limit, next-open backtest


# Tree settings the daily tune-up chooses between (see tune_and_train). "base" is the researched default.
CONFIGS = {
    "base": dict(max_iter=250, learning_rate=0.04, max_leaf_nodes=31, min_samples_leaf=300),
    "steady": dict(max_iter=300, learning_rate=0.03, max_leaf_nodes=15, min_samples_leaf=600),
    "sharp": dict(max_iter=250, learning_rate=0.05, max_leaf_nodes=63, min_samples_leaf=200),
}
HOLDOUT_DAYS = 120  # newest labelled days kept back to test each candidate on unseen data
SWITCH_MARGIN = 0.005  # a challenger must beat the current settings by this much (rank correlation) to replace them
ACTIVE = {"config": "base"}


def _hgb_reg():
    return HistGradientBoostingRegressor(**CONFIGS[ACTIVE["config"]], l2_regularization=1.0, random_state=7,
                                         loss="absolute_error")


def _hgb_clf():
    return HistGradientBoostingClassifier(**CONFIGS[ACTIVE["config"]], l2_regularization=1.0, random_state=7)


def _clean(o):
    if isinstance(o, float):
        return o if np.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating,)):
        return _clean(float(o))
    if isinstance(o, (np.integer,)):
        return int(o)
    return o


def dump(obj, path):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(_clean(obj), f, allow_nan=False)
    os.replace(tmp, path)


class Paths:
    def __init__(self, root):
        self.root = root
        self.prices = os.path.join(root, "data", "prices")
        self.meta = os.path.join(root, "data", "meta")
        self.out = os.path.join(root, "data", "out")
        self.stock = os.path.join(self.out, "stock")
        self.records = os.path.join(root, "data", "records")
        self.models = os.path.join(root, "data", "models")
        for p in [self.prices, self.meta, self.out, self.stock, self.records, self.models]:
            os.makedirs(p, exist_ok=True)


def load_meta(p: Paths) -> dict:
    path = os.path.join(p.meta, "sectors.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"sector": {}, "name": {}}


# ------------------------------------------------------------------ panel
def build_panel(p: Paths):
    meta = load_meta(p)
    parts, events, raw = [], {}, {}
    for f in glob.glob(os.path.join(p.prices, "*.csv")):
        sym = os.path.basename(f)[:-4]
        try:
            d = load_symbol_csv(f)
        except Exception:
            continue
        if len(d) < 60:
            continue
        d, ev = adjust_corporate_actions(d)
        fe = stock_features(d)
        fe["symbol"] = sym
        fe["sector"] = meta["sector"].get(sym, "Other")
        fe["raw_open"], fe["raw_high"], fe["raw_low"] = d["open"], d["high"], d["low"]
        fe["adj_open"], fe["adj_high"], fe["adj_low"] = d["adj_open"], d["adj_high"], d["adj_low"]
        fe["volume"], fe["amount"] = d["traded_quantity"], d["traded_amount"]
        parts.append(fe)
        events[sym] = ev
    panel = pd.concat(parts, ignore_index=True)
    panel = panel[panel["date"] >= "2011-01-01"]
    panel, mkt = add_market_features(panel)
    panel["tradable"] = M.tradable(panel)
    for h in [10, 20]:
        col = f"fwd{h}"
        mean = panel[panel["tradable"]].groupby("date")[col].mean()
        panel[f"xs{h}"] = panel[col] - panel["date"].map(mean)
    return panel, mkt, events, meta


# ------------------------------------------------------------------ models
MODEL_SPECS = [("xs20", "reg"), ("xs20", "clf"), ("xs10", "reg")]


def _fit_one(train, label, kind):
    if kind == "reg":
        return _hgb_reg().fit(train[FEATS], train[label].clip(-0.3, 0.3))
    y = (train.groupby("date")[label].rank(pct=True) > 0.8).astype(int)
    return _hgb_clf().fit(train[FEATS], y)


def _predict_one(m, kind, X):
    return m.predict_proba(X)[:, 1] if kind == "clf" else m.predict(X)


def fit_ensemble(train):
    out = {}
    for label, kind in MODEL_SPECS:
        tr = train[train[label].notna()]
        out[f"{label}_{kind}"] = (_fit_one(tr, label, kind), kind)
    return out


def score(models, df):
    """Adds raw model outputs and the ensemble score (avg of within-day percentile ranks)."""
    df = df.copy()
    for name, (m, kind) in models.items():
        df[name] = _predict_one(m, kind, df[FEATS])
    ranks = [df.groupby("date")[name].rank(pct=True) for name in models]
    df["score"] = sum(ranks) / len(ranks)
    return df


def holdout_check(train, test) -> dict:
    """Fit on `train`, score the unseen `test` days: how well does the score rank next-20-day winners?"""
    sc = score(fit_ensemble(train), test)
    sc = sc[sc["xs20"].notna()]
    ic = sc.groupby("date").apply(lambda d: d["score"].corr(d["xs20"], method="spearman")).mean()
    top = sc[sc.groupby("date")["score"].rank(ascending=False) <= TOP_N]
    return {"ic": round(float(ic), 4), "top_excess": round(float(top["xs20"].mean()) * 100, 2),
            "top_beat": round(float((top["xs20"] > 0).mean()) * 100, 1)}


def tune_and_train(p: Paths, panel, current="base") -> dict:
    """Daily tune-up: test each candidate setting on the newest unseen months, keep the best, retrain on everything."""
    data = panel[panel["tradable"]]
    # embargo: labels need 20 future days -> drop last ~30 days from training
    cutoff = data["date"].max() - pd.Timedelta(days=30)
    hold_from = cutoff - pd.Timedelta(days=HOLDOUT_DAYS * 7 // 5)
    train = data[data["date"] < hold_from - pd.Timedelta(days=30)]
    test = data[(data["date"] >= hold_from) & (data["date"] < cutoff)]
    results = {}
    for name in CONFIGS:
        ACTIVE["config"] = name
        results[name] = holdout_check(train, test)
        log.info("tune %s -> %s", name, results[name])
    best = max(results, key=lambda k: results[k]["ic"])
    chosen = current if current in results and results[best]["ic"] - results[current]["ic"] < SWITCH_MARGIN else best
    ACTIVE["config"] = chosen
    models = fit_ensemble(data[data["date"] < cutoff])
    info = {"models": models, "trained": datetime.now().isoformat(), "version": MODEL_VERSION, "config": chosen,
            "train_until": cutoff.strftime("%Y-%m-%d"), "rows": int((data["date"] < cutoff).sum()),
            "holdout": results}
    joblib.dump(info, os.path.join(p.models, "ensemble.joblib"))
    hist_path = os.path.join(p.out, "model_history.json")
    try:
        with open(hist_path) as f:
            hist = json.load(f)
    except Exception:
        hist = []
    hist = [h for h in hist if h["date"] != info["trained"][:10]] + [{
        "date": info["trained"][:10], "config": chosen, "switched": chosen != current, "rows": info["rows"],
        "train_until": info["train_until"], **results[chosen]}]
    dump(hist[-400:], hist_path)
    return info


def load_models(p: Paths):
    path = os.path.join(p.models, "ensemble.joblib")
    if not os.path.exists(path):
        return None, None
    try:
        obj = joblib.load(path)
    except Exception:  # e.g. saved with a different scikit-learn version -> retrain
        return None, None
    if obj.get("version") != MODEL_VERSION:
        return None, None
    ACTIVE["config"] = obj.get("config", "base")
    return obj["models"], obj


# ------------------------------------------------------------------ backtest (walk-forward, honest)
FEE_SIDE = 0.0040
SLIPPAGE = 0.0050  # paying a little above the open / selling a little below it
TAX = 0.075


def simulate(scores: pd.DataFrame, G: pd.DataFrame, I: pd.DataFrame, breadth: pd.Series, start="2016-01-01",
             n=TOP_N, exit_rank=EXIT_RANK, rebalance=REBALANCE_DAYS, regime=True):
    """Realistic execution: a signal from day d's close is traded at the OPEN of the next trading day.

    G = overnight return (open_t / close_t-1 - 1), I = open-to-close return (close_t / open_t - 1).
    """
    S = scores.pivot_table(index="date", columns="symbol", values="score").sort_index().reindex(G.index).shift(1)
    br = breadth.reindex(G.index).shift(1)
    cost = FEE_SIDE + SLIPPAGE
    dates = G.index[G.index >= start]
    hold, cash = {}, 1.0
    curve, trades = [], []
    for i, d in enumerate(dates):
        g, it = G.loc[d], I.loc[d]
        for s in hold:
            hold[s]["v"] *= 1 + g.get(s, 0.0)
        if i % rebalance == 0:
            row = S.loc[d].dropna()
            rk = row.rank(ascending=False)
            risk_on = (not regime) or br.get(d, 0.5) > BREADTH_RISK_ON
            if not row.empty:
                for s in list(hold):
                    h = hold[s]
                    if s not in rk.index or rk[s] > exit_rank or not risk_on:
                        v = h["v"] * (1 - cost)
                        pnl = v - h["cost"]
                        if pnl > 0:
                            v -= pnl * TAX
                        cash += v
                        trades.append({"symbol": s, "buy": h["d"].strftime("%Y-%m-%d"),
                                       "sell": d.strftime("%Y-%m-%d"), "ret": round(v / h["cost"] - 1, 4)})
                        del hold[s]
            if risk_on and not row.empty:
                port = sum(h["v"] for h in hold.values()) + cash
                for s in rk.sort_values().index:
                    if len(hold) >= n or rk[s] > n:
                        break
                    if s in hold:
                        continue
                    alloc = min(port / n, cash)
                    if alloc <= 1e-9:
                        break
                    cash -= alloc
                    hold[s] = {"v": alloc * (1 - cost), "cost": alloc, "d": d}
        for s in hold:
            hold[s]["v"] *= 1 + it.get(s, 0.0)
        curve.append((d, sum(h["v"] for h in hold.values()) + cash))
    return pd.Series(dict(curve)), trades


def _stats(c: pd.Series) -> dict:
    yrs = max((c.index[-1] - c.index[0]).days / 365.25, 0.1)
    return {"cagr": round(float(c.iloc[-1] ** (1 / yrs) - 1), 4),
            "total": round(float(c.iloc[-1] - 1), 4),
            "max_drawdown": round(float((c / c.cummax() - 1).min()), 4)}


def run_backtest(p: Paths, panel, mkt) -> dict:
    t = time.time()
    data = panel[panel["tradable"]]
    preds = []
    years = range(2016, int(panel["date"].dt.year.max()) + 1)
    for y in years:
        cut = pd.Timestamp(f"{y}-01-01") - pd.Timedelta(days=40)
        tr = data[data["date"] < cut]
        te = data[data["date"].dt.year == y]
        if te.empty or len(tr) < 10000:
            continue
        models = fit_ensemble(tr)
        preds.append(score(models, te)[["date", "symbol", "score", "fwd20", "fwd20_open", "xs20"]])
    pr = pd.concat(preds)
    R = panel.pivot_table(index="date", columns="symbol", values="ret1").sort_index().clip(-0.16, 0.16).fillna(0)
    O = panel.pivot_table(index="date", columns="symbol", values="adj_open").sort_index()
    C = panel.pivot_table(index="date", columns="symbol", values="adj_close").sort_index()
    G = (O / C.ffill().shift(1) - 1).clip(-0.16, 0.16).fillna(0)
    I = (C / O - 1).clip(-0.16, 0.16).fillna(0)
    breadth = mkt["breadth50"]
    strat, trades = simulate(pr, G, I, breadth)
    no_timing, _ = simulate(pr, G, I, breadth, regime=False)
    # benchmark: buy every tradable stock on Jan 1 each year, hold
    tr_ = panel[panel["tradable"]]
    bench_parts, base = [], 1.0
    for y in years:
        dy = tr_[tr_["date"].dt.year == y]
        if dy.empty:
            continue
        syms = [s for s in dy[dy["date"] == dy["date"].min()]["symbol"] if s in R.columns]
        g = (1 + R.loc[R.index.year == y, syms]).cumprod().mean(axis=1) * base
        base = float(g.iloc[-1])
        bench_parts.append(g)
    bench = pd.concat(bench_parts)
    bench = bench[bench.index >= strat.index[0]] / bench[bench.index >= strat.index[0]].iloc[0]
    # yearly returns
    yearly = []
    for y in years:
        s, b = strat[strat.index.year == y], bench[bench.index.year == y]
        if s.empty:
            continue
        prev_s = strat[strat.index < s.index[0]]
        prev_b = bench[bench.index < b.index[0]]
        s0 = prev_s.iloc[-1] if len(prev_s) else 1.0
        b0 = prev_b.iloc[-1] if len(prev_b) else 1.0
        yearly.append({"year": int(y), "model": round(float(s.iloc[-1] / s0 - 1), 4),
                       "market": round(float(b.iloc[-1] / b0 - 1), 4)})
    # pick quality by decile
    # (skip days with < 10 stocks, e.g. stray rows on a holiday — they can't be split into deciles)
    dq = pr[pr.groupby("date")["score"].transform("size") >= 10].copy()
    dq["dec"] = dq.groupby("date")["score"].transform(lambda x: pd.qcut(x.rank(method="first"), 10, labels=False))
    deciles = dq.groupby("dec")["fwd20_open"].mean().mul(100).round(2).tolist()
    top5 = pr[pr.groupby("date")["score"].rank(ascending=False) <= 5]
    # how the daily top picks really did over the next 20 trading days, bought at the next open
    pk = top5.dropna(subset=["fwd20_open"]).copy()
    pk["mkt"] = pk["date"].map(pr.groupby("date")["fwd20_open"].mean())
    pick_stats = {"n": int(len(pk)), "beat_market": round(float((pk["fwd20_open"] > pk["mkt"]).mean()), 3),
                  "profitable": round(float((pk["fwd20_open"] > 2 * (FEE_SIDE + SLIPPAGE)).mean()), 3),
                  "avg": round(float(pk["fwd20_open"].mean()), 4), "market_avg": round(float(pk["mkt"].mean()), 4)}
    tr_arr = np.array([x["ret"] for x in trades]) if trades else np.array([0.0])
    wk = strat.resample("W").last()
    res = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "seconds": round(time.time() - t),
        "period": [strat.index[0].strftime("%Y-%m-%d"), strat.index[-1].strftime("%Y-%m-%d")],
        "strategy": _stats(strat), "no_market_filter": _stats(no_timing), "buy_and_hold": _stats(bench),
        "trades": {"count": len(trades), "per_year": round(len(trades) / max(len(yearly), 1), 1),
                   "win_rate": round(float((tr_arr > 0).mean()), 3), "avg": round(float(tr_arr.mean()), 4),
                   "avg_win": round(float(tr_arr[tr_arr > 0].mean()) if (tr_arr > 0).any() else 0, 4),
                   "avg_loss": round(float(tr_arr[tr_arr <= 0].mean()) if (tr_arr <= 0).any() else 0, 4),
                   "recent": trades[-25:][::-1]},
        "top5_hit_rate_20d": round(float((top5["xs20"] > 0).mean()), 3),
        "pick_stats": pick_stats,
        "deciles_fwd20": deciles,
        "yearly": yearly,
        "curve": {"dates": [d.strftime("%Y-%m-%d") for d in wk.index],
                  "model": [round(float(x), 4) for x in wk.values],
                  "market": [round(float(x), 4) for x in bench.resample("W").last().reindex(wk.index).ffill().values],
                  "no_filter": [round(float(x), 4) for x in no_timing.resample("W").last().reindex(wk.index).ffill().values]},
        "settings": {"top_n": TOP_N, "exit_rank": EXIT_RANK, "rebalance_days": REBALANCE_DAYS,
                     "breadth_risk_on": BREADTH_RISK_ON, "fee_per_side": FEE_SIDE, "slippage": SLIPPAGE, "cgt": TAX,
                     "execution": "next day's open"},
    }
    dump(res, os.path.join(p.out, "backtest.json"))
    return res


# ------------------------------------------------------------------ signals
def _reasons(r) -> tuple[list[str], list[str]]:
    good, bad = [], []
    if r["px_sma50"] > 0 and r["sma50_slope"] > 0:
        good.append("Uptrend: price above a rising 50-day average")
    elif r["px_sma50"] < 0 and r["sma50_slope"] < 0:
        bad.append("Downtrend: price below a falling 50-day average")
    if r["px_sma200"] > 0:
        good.append("Above 200-day average (long-term trend up)")
    else:
        bad.append("Below 200-day average (long-term trend down)")
    if r["rs20"] > 0.03:
        good.append(f"Beating the market by {r['rs20']*100:.1f}% over 20 days")
    elif r["rs20"] < -0.03:
        bad.append(f"Lagging the market by {abs(r['rs20'])*100:.1f}% over 20 days")
    if r["vol_ratio5"] > 1.5:
        (good if r["ret5"] > 0 else bad).append(
            f"Volume {r['vol_ratio5']:.1f}x normal with price {'rising' if r['ret5'] > 0 else 'falling'}")
    if r["rsi14"] > 75:
        bad.append(f"RSI {r['rsi14']:.0f}: overbought, may pull back — avoid chasing")
    elif r["rsi14"] < 30:
        bad.append(f"RSI {r['rsi14']:.0f}: oversold, still falling")
    elif 50 <= r["rsi14"] <= 70:
        good.append(f"RSI {r['rsi14']:.0f}: healthy momentum")
    if r["hi20_break"] > 0:
        good.append("Broke out to a new 20-day high")
    if r["dist_hi52"] > -0.05:
        good.append("Near its 52-week high")
    if r["macd_hist"] > 0:
        good.append("MACD positive (momentum improving)")
    else:
        bad.append("MACD negative (momentum weakening)")
    return good, bad


def _f(x, nd=2):
    try:
        if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
            return None
        return round(float(x), nd)
    except Exception:
        return None


def make_signals(p: Paths, panel, mkt, models, meta, events, sources=None) -> dict:
    latest = panel["date"].max()
    recent = panel[panel["date"] >= latest - pd.Timedelta(days=400)].copy()
    sc = score(models, recent[recent["tradable"]])
    recent = recent.merge(sc[["date", "symbol", "score", "xs20_reg", "xs20_clf", "xs10_reg"]],
                          on=["date", "symbol"], how="left")
    recent["rank"] = recent.groupby("date")["score"].rank(ascending=False)
    last = recent.sort_values("date").groupby("symbol").tail(1)
    last = last[last["date"] >= latest - pd.Timedelta(days=10)]  # still trading
    m = mkt.iloc[-1]
    breadth = float(m["breadth50"])
    risk_on = breadth > BREADTH_RISK_ON
    if breadth > 0.6:
        mood = "Strong"
    elif breadth > BREADTH_RISK_ON:
        mood = "Positive"
    elif breadth > 0.25:
        mood = "Weak"
    else:
        mood = "Very weak"

    rows = []
    for _, r in last.iterrows():
        rank = r["rank"] if r["date"] == latest else np.nan
        if not M.is_share(pd.DataFrame([r])).iloc[0]:
            action, why = "AVOID", "Not an ordinary share (debenture, promoter share or mutual fund)"
        elif not r["tradable"]:
            action, why = "AVOID", "Low trading volume (hard to buy/sell)"
        elif np.isnan(rank):
            action, why = "AVOID", "Did not trade on the latest day"
        elif not risk_on:
            action, why = ("SELL" if rank > EXIT_RANK else "HOLD"), "Market is weak — model says stay mostly in cash"
        elif rank <= TOP_N:
            action, why = "BUY", f"Top {TOP_N} model pick"
        elif rank <= 15:
            action, why = "WATCH", "Close to a buy — watch for entry"
        elif rank <= EXIT_RANK:
            action, why = "HOLD", "Fine to keep if you own it"
        else:
            action, why = "SELL", "Weak ranking — sell if you own it"
        good, bad = _reasons(r)
        atr = r["atr14"] if pd.notna(r["atr14"]) else r["close"] * 0.03
        price = float(r["close"])
        rows.append({
            "symbol": r["symbol"], "name": meta.get("name", {}).get(r["symbol"]) or "",
            "sector": r["sector"], "date": r["date"].strftime("%Y-%m-%d"),
            "price": _f(price), "chg1": _f(r["ret1"] * 100), "chg5": _f(r["ret5"] * 100), "chg20": _f(r["ret20"] * 100),
            "chg60": _f(r["ret60"] * 100), "chg1y": None,
            "volume": _f(r["volume"], 0), "turnover": _f(r["amount"], 0), "turnover20": _f(r["turnover20"], 0),
            "rsi": _f(r["rsi14"], 1), "px_sma50": _f(r["px_sma50"] * 100), "px_sma200": _f(r["px_sma200"] * 100),
            "dist_hi52": _f(r["dist_hi52"] * 100), "vol_ratio": _f(r["vol_ratio5"]),
            "score": _f((r["score"] or 0) * 100, 1) if pd.notna(r["score"]) else None,
            "rank": int(rank) if pd.notna(rank) else None,
            "exp_excess20": _f(r["xs20_reg"] * 100) if pd.notna(r["xs20_reg"]) else None,
            "prob_top20": _f(r["xs20_clf"] * 100, 1) if pd.notna(r["xs20_clf"]) else None,
            "action": action, "action_reason": why, "good": good, "bad": bad,
            "entry_max": _f(price * 1.02), "stop": _f(price - 2.5 * atr), "target": _f(price + 3 * atr),
            "tradable": bool(r["tradable"]),
        })
    rows.sort(key=lambda x: (x["rank"] is None, x["rank"] or 9999, x["symbol"]))

    # 1-year change per symbol
    yr = panel[panel["date"] >= latest - pd.Timedelta(days=365)].groupby("symbol")["adj_close"].agg(["first", "last"])
    for x in rows:
        if x["symbol"] in yr.index:
            a = yr.loc[x["symbol"]]
            x["chg1y"] = _f((a["last"] / a["first"] - 1) * 100)

    # per-stock detail files
    hist = panel[panel["date"] >= latest - pd.Timedelta(days=3 * 365)]
    sc_hist = recent[["date", "symbol", "score", "rank"]]
    for sym, g in hist.groupby("symbol"):
        g = g.sort_values("date")
        s = sc_hist[sc_hist["symbol"] == sym].set_index("date")
        detail = {
            "symbol": sym, "name": meta.get("name", {}).get(sym) or "", "sector": meta["sector"].get(sym, "Other"),
            "dates": g["date"].dt.strftime("%Y-%m-%d").tolist(),
            "open": [_f(x) for x in g["adj_open"]], "high": [_f(x) for x in g["adj_high"]],
            "low": [_f(x) for x in g["adj_low"]], "close": [_f(x) for x in g["adj_close"]],
            "volume": [_f(x, 0) for x in g["volume"]],
            "sma20": [_f(x) for x in g["sma20"]], "sma50": [_f(x) for x in g["sma50"]],
            "sma200": [_f(x) for x in g["sma200"]], "rsi": [_f(x, 1) for x in g["rsi14"]],
            "score": [_f(s["score"].get(d) * 100, 1) if d in s.index and pd.notna(s["score"].get(d)) else None
                      for d in g["date"]],
            "events": events.get(sym, [])[-12:],
        }
        dump(detail, os.path.join(p.stock, f"{sym}.json"))

    # market proxy (equal-weight liquid stocks) + breadth
    mk = mkt[mkt.index >= latest - pd.Timedelta(days=2 * 365)]
    today = [x for x in rows if x["date"] == latest.strftime("%Y-%m-%d") and x["tradable"]]
    gainers = sorted([x for x in rows if x["date"] == latest.strftime("%Y-%m-%d") and x["chg1"] is not None],
                     key=lambda x: -x["chg1"])
    official = None
    try:
        with open(os.path.join(p.meta, "nepse_index.json")) as f:
            official = json.load(f)
    except Exception:
        pass
    summary = {
        "as_of": latest.strftime("%Y-%m-%d"),
        "generated": datetime.now().isoformat(timespec="seconds"),
        "market": {
            "mood": mood, "risk_on": bool(risk_on), "breadth50": round(breadth * 100, 1),
            "breadth200": round(float(m["breadth200"]) * 100, 1),
            "advances": int(m["adv"]), "declines": int(m["dec"]), "stocks": int(m["n"]),
            "proxy_chg1": _f(m["mkt_r1"] * 100), "proxy_chg20": _f(m["mkt_ret20"] * 100),
            "official_index": official,
            "series": {"dates": mk.index.strftime("%Y-%m-%d").tolist(),
                       "index": [_f(x) for x in mk["mkt_index"]],
                       "breadth": [_f(x * 100, 1) for x in mk["breadth50"]]},
        },
        "buys": [x for x in rows if x["action"] == "BUY"],
        "watch": [x for x in rows if x["action"] == "WATCH"][:10],
        "gainers": gainers[:10], "losers": gainers[::-1][:10],
        "top_turnover": sorted(today, key=lambda x: -(x["turnover"] or 0))[:10],
        "sources": sources or [],
        "settings": {"top_n": TOP_N, "exit_rank": EXIT_RANK, "breadth_risk_on": BREADTH_RISK_ON * 100},
    }
    dump(summary, os.path.join(p.out, "summary.json"))
    dump(rows, os.path.join(p.out, "stocks.json"))

    # daily record (one file per market day; never overwritten once the day is recorded)
    rec_path = os.path.join(p.records, f"{summary['as_of']}.json")
    rec = {
        "date": summary["as_of"], "market": {k: summary["market"][k] for k in
                                             ["mood", "risk_on", "breadth50", "advances", "declines", "proxy_chg1"]},
        "buys": [{k: x[k] for k in ["symbol", "price", "score", "rank", "stop", "target"]} for x in summary["buys"]],
        "top30": [{k: x[k] for k in ["symbol", "price", "score", "rank", "action"]} for x in rows if x["rank"] and x["rank"] <= 30],
        "sells_top_turnover": [x["symbol"] for x in rows if x["action"] == "SELL" and x["tradable"]][:30],
    }
    if not os.path.exists(rec_path):
        dump(rec, rec_path)
    build_track_record(p, {x["symbol"]: x["price"] for x in rows}, latest, mkt["mkt_index"])
    return summary


def build_track_record(p: Paths, prices: dict, latest, mkt_index=None):
    """How did past BUY calls do since they were made, and did they beat the market over the same days?"""
    out = []
    mkt_now = float(mkt_index.iloc[-1]) if mkt_index is not None and len(mkt_index) else None
    for f in sorted(glob.glob(os.path.join(p.records, "*.json")))[-120:]:
        with open(f) as fh:
            rec = json.load(fh)
        picks = []
        for b in rec.get("buys", []):
            now = prices.get(b["symbol"])
            if now and b.get("price"):
                picks.append({**b, "now": now, "ret": round((now / b["price"] - 1) * 100, 2)})
        rets = [x["ret"] for x in picks]
        mret = None
        if mkt_now and pd.Timestamp(rec["date"]) in mkt_index.index:
            mret = round((mkt_now / float(mkt_index.loc[pd.Timestamp(rec["date"])]) - 1) * 100, 2)
        out.append({"date": rec["date"], "market": rec["market"], "picks": picks, "market_ret": mret,
                    "avg_ret": round(float(np.mean(rets)), 2) if rets else None})
    out.reverse()
    # live scorecard: real picks (made before the fact), each compared with the market over the same days
    scored = [(x["ret"], r["market_ret"]) for r in out if r["market_ret"] is not None for x in r["picks"]]
    live = {"picks": len(scored), "days": sum(1 for r in out if r["picks"]),
            "avg": round(float(np.mean([a for a, _ in scored])), 2) if scored else None,
            "market": round(float(np.mean([m for _, m in scored])), 2) if scored else None,
            "beat": round(float(np.mean([a > m for a, m in scored])) * 100, 0) if scored else None,
            "since": out[-1]["date"] if out else None}
    dump({"as_of": latest.strftime("%Y-%m-%d"), "records": out, "live": live}, os.path.join(p.out, "records.json"))


# ------------------------------------------------------------------ orchestration
def run(root: str, fetch=True, retrain=False, backtest=False, skip_github=False) -> dict:
    from .sources import update_all

    p = Paths(root)
    t0 = time.time()
    sources = update_all(p.prices, p.meta, skip_github=skip_github) if fetch else []
    panel, mkt, events, meta = build_panel(p)
    models, info = load_models(p)
    age_days = 999
    if info:
        age_days = (datetime.now() - datetime.fromisoformat(info["trained"])).days
    trained_on = info.get("train_until") if info else None
    newest_cut = (panel["date"].max() - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    # retrain whenever a new trading day of data has arrived, so the model keeps learning from the latest market
    if models is None or retrain or age_days >= 1 or trained_on != newest_cut:
        log.info("training models ...")
        info = tune_and_train(p, panel, current=(info or {}).get("config", "base"))
        models = info["models"]
    bt_path = os.path.join(p.out, "backtest.json")
    bt_age = (time.time() - os.path.getmtime(bt_path)) / 86400 if os.path.exists(bt_path) else 999
    if backtest or retrain or bt_age >= 7:  # the full 10-year test is slow, so it reruns weekly
        log.info("running walk-forward backtest ...")
        run_backtest(p, panel, mkt)
    summary = make_signals(p, panel, mkt, models, meta, events, sources)
    status = {"last_run": datetime.now().isoformat(timespec="seconds"), "as_of": summary["as_of"],
              "seconds": round(time.time() - t0), "sources": sources}
    dump(status, os.path.join(p.out, "status.json"))
    return status


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--retrain", action="store_true")
    ap.add_argument("--backtest", action="store_true")
    a = ap.parse_args()
    print(run(a.root, fetch=not a.no_fetch, retrain=a.retrain, backtest=a.backtest))
