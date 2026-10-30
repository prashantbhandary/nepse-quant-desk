"""Reproduce every number in docs/RESEARCH.md.

Run from the project root:  .venv/bin/python -m research.reproduce
Writes research/out/results.json and research/out/*.csv.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from engine import pipeline as P

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "research", "out")
os.makedirs(OUT, exist_ok=True)
RNG = np.random.default_rng(7)


def cagr(c: pd.Series) -> float:
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    return float((c.iloc[-1] / c.iloc[0]) ** (1 / yrs) - 1)


def mdd(c: pd.Series) -> float:
    return float((c / c.cummax() - 1).min())


def stats(c: pd.Series) -> dict:
    r = c.pct_change().dropna()
    return {"cagr": cagr(c), "max_dd": mdd(c), "vol": float(r.std() * np.sqrt(240)),
            "sharpe": float(r.mean() / r.std() * np.sqrt(240)) if r.std() > 0 else 0.0}


def period(c: pd.Series, a: str, b: str) -> dict:
    s = c[(c.index >= a) & (c.index <= b)]
    return stats(s / s.iloc[0])


def nw_tstat(x: np.ndarray, lags: int) -> float:
    m = x.mean()
    e = x - m
    n = len(x)
    v = e @ e / n
    for k in range(1, lags + 1):
        v += 2 * (1 - k / (lags + 1)) * (e[k:] @ e[:-k]) / n
    return float(m / np.sqrt(v / n))


def block_bootstrap(diff: np.ndarray, block=20, reps=2000) -> tuple[float, float, float]:
    """Moving-block bootstrap of the mean daily excess return, annualised (x240)."""
    n = len(diff)
    k = int(np.ceil(n / block))
    means = np.empty(reps)
    for i in range(reps):
        starts = RNG.integers(0, n - block, k)
        idx = (starts[:, None] + np.arange(block)).ravel()[:n]
        means[i] = diff[idx].mean() * 240
    return float(diff.mean() * 240), float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def main():
    p = P.Paths(ROOT)
    panel, mkt, _, _ = P.build_panel(p)
    data = panel[panel["tradable"]]
    years = range(2016, int(panel["date"].dt.year.max()) + 1)

    # ---------------- walk-forward predictions (identical to P.run_backtest)
    preds, fitted = [], {}
    for y in years:
        cut = pd.Timestamp(f"{y}-01-01") - pd.Timedelta(days=40)
        tr, te = data[data["date"] < cut], data[data["date"].dt.year == y]
        if te.empty or len(tr) < 10000:
            continue
        models = P.fit_ensemble(tr)
        fitted[y] = models
        preds.append(P.score(models, te)[["date", "symbol", "score", "fwd20", "fwd20_open", "xs20",
                                          "rank_ret60", "ret20"]])
        print("fitted", y, len(tr), flush=True)
    pr = pd.concat(preds)

    R = panel.pivot_table(index="date", columns="symbol", values="ret1").sort_index().clip(-0.16, 0.16).fillna(0)
    O = panel.pivot_table(index="date", columns="symbol", values="adj_open").sort_index()
    C = panel.pivot_table(index="date", columns="symbol", values="adj_close").sort_index()
    G = (O / C.ffill().shift(1) - 1).clip(-0.16, 0.16).fillna(0)
    I = (C / O - 1).clip(-0.16, 0.16).fillna(0)
    br = mkt["breadth50"]

    strat, trades = P.simulate(pr, G, I, br)
    nofilt, _ = P.simulate(pr, G, I, br, regime=False)

    tr_ = panel[panel["tradable"]]
    parts, base = [], 1.0
    for y in years:
        dy = tr_[tr_["date"].dt.year == y]
        if dy.empty:
            continue
        syms = [s for s in dy[dy["date"] == dy["date"].min()]["symbol"] if s in R.columns]
        g = (1 + R.loc[R.index.year == y, syms]).cumprod().mean(axis=1) * base
        base = float(g.iloc[-1])
        parts.append(g)
    bench = pd.concat(parts)
    bench = bench[bench.index >= strat.index[0]]
    bench = bench / bench.iloc[0]

    res = {"period": [str(strat.index[0].date()), str(strat.index[-1].date())]}

    # ---------------- data description
    yr = tr_.assign(y=tr_["date"].dt.year).groupby(["y", "date"]).size().groupby("y").mean()
    res["data"] = {"symbols_total": int(panel["symbol"].nunique()),
                   "first_date": str(panel["date"].min().date()), "last_date": str(panel["date"].max().date()),
                   "rows": int(len(panel)), "tradable_rows": int(len(tr_)),
                   "tradable_per_day": {int(k): round(float(v), 1) for k, v in yr.items()},
                   "trading_days_test": int(len(strat))}
    pd.DataFrame({"year": yr.index, "n": yr.values.round(1)}).to_csv(f"{OUT}/universe.csv", index=False)

    # ---------------- headline + sub-periods
    sub = [("2016-01-01", "2020-12-31"), ("2021-01-01", "2026-12-31"), ("2016-01-01", "2026-12-31")]
    res["headline"] = {}
    for name, c in [("strategy", strat), ("no_filter", nofilt), ("buy_hold", bench)]:
        res["headline"][name] = {f"{a[:4]}-{b[:4]}": period(c, a, b) for a, b in sub}

    # ---------------- excess return test (daily, bootstrap)
    sr, br_ = strat.pct_change().dropna(), bench.pct_change().dropna()
    j = sr.index.intersection(br_.index)
    diff = (sr[j] - br_[j]).values
    res["excess_bootstrap"] = {}
    for a, b in sub:
        m = (j >= a) & (j <= b)
        mean, lo, hi = block_bootstrap(diff[m])
        res["excess_bootstrap"][f"{a[:4]}-{b[:4]}"] = {"mean_ann": mean, "ci_lo": lo, "ci_hi": hi}

    # ---------------- information coefficient
    pr_ok = pr[pr.groupby("date")["score"].transform("size") >= 10]
    ic = pr_ok.dropna(subset=["fwd20_open"]).groupby("date").apply(
        lambda d: d["score"].corr(d["fwd20_open"], method="spearman")).dropna()
    mom = pr_ok.dropna(subset=["fwd20_open"]).groupby("date").apply(
        lambda d: d["rank_ret60"].corr(d["fwd20_open"], method="spearman")).dropna()
    res["ic"] = {"mean": float(ic.mean()), "std": float(ic.std()), "icir_ann": float(ic.mean() / ic.std() * np.sqrt(12)),
                 "pos_share": float((ic > 0).mean()), "t_nw20": nw_tstat(ic.values, 20), "days": int(len(ic)),
                 "momentum_mean": float(mom.mean()), "momentum_t_nw20": nw_tstat(mom.values, 20)}
    icy = pd.DataFrame({"model": ic.groupby(ic.index.year).mean(), "momentum": mom.groupby(mom.index.year).mean()})
    icy.index.name = "year"
    icy.round(4).to_csv(f"{OUT}/ic_year.csv")
    res["ic_by_year"] = {int(k): round(float(v), 4) for k, v in icy["model"].items()}

    # ---------------- deciles
    dq = pr_ok.dropna(subset=["fwd20_open"]).copy()
    dq["dec"] = dq.groupby("date")["score"].transform(lambda x: pd.qcut(x.rank(method="first"), 10, labels=False))
    dm = dq.groupby("dec")["fwd20_open"].mean() * 100
    pd.DataFrame({"decile": np.arange(1, 11), "ret": dm.values.round(3)}).to_csv(f"{OUT}/deciles.csv", index=False)
    spread = dq[dq["dec"] == 9].groupby("date")["fwd20_open"].mean() - dq[dq["dec"] == 0].groupby("date")["fwd20_open"].mean()
    spread = spread.dropna()
    res["deciles"] = {"ret_pct": [round(float(x), 3) for x in dm.values], "spread_mean_pct": float(spread.mean() * 100),
                      "spread_t_nw20": nw_tstat(spread.values, 20),
                      "monotonic_spearman": float(pd.Series(dm.values).corr(pd.Series(range(10)), method="spearman"))}

    # ---------------- trades
    t = np.array([x["ret"] for x in trades])
    hold = np.array([(pd.Timestamp(x["sell"]) - pd.Timestamp(x["buy"])).days for x in trades])
    res["trades"] = {"count": int(len(t)), "win_rate": float((t > 0).mean()), "avg": float(t.mean()),
                     "median": float(np.median(t)), "avg_win": float(t[t > 0].mean()), "avg_loss": float(t[t <= 0].mean()),
                     "hold_days_median": float(np.median(hold))}

    # ---------------- robustness: number of holdings
    res["top_n"] = {}
    for n in [1, 2, 3, 5, 10]:
        c, _ = P.simulate(pr, G, I, br, n=n)
        res["top_n"][n] = {f"{a[:4]}-{b[:4]}": period(c, a, b) for a, b in sub}
        print("top_n", n, flush=True)

    # ---------------- robustness: costs
    res["costs"] = {}
    f0, s0 = P.FEE_SIDE, P.SLIPPAGE
    for mult in [0.0, 0.5, 1.0, 1.5, 2.0]:
        P.FEE_SIDE, P.SLIPPAGE = f0 * mult, s0 * mult
        c, _ = P.simulate(pr, G, I, br)
        res["costs"][str(mult)] = {"cost_side": (f0 + s0) * mult, **period(c, "2016-01-01", "2026-12-31")}
    P.FEE_SIDE, P.SLIPPAGE = f0, s0
    print("costs done", flush=True)

    # ---------------- baselines: momentum and random ranking, same trading rules
    mom_pr = pr.assign(score=pr["rank_ret60"])
    cm, _ = P.simulate(mom_pr, G, I, br)
    res["momentum"] = {f"{a[:4]}-{b[:4]}": period(cm, a, b) for a, b in sub}
    rand = []
    for i in range(100):
        rp = pr.assign(score=RNG.random(len(pr)))
        c, _ = P.simulate(rp, G, I, br)
        rand.append(cagr(c))
        if i % 20 == 0:
            print("random", i, flush=True)
    rand = np.array(rand)
    res["random"] = {"n": len(rand), "mean": float(rand.mean()), "p5": float(np.percentile(rand, 5)),
                     "p50": float(np.median(rand)), "p95": float(np.percentile(rand, 95)),
                     "share_below_model": float((rand < cagr(strat)).mean())}
    pd.DataFrame({"cagr": (rand * 100).round(3)}).to_csv(f"{OUT}/random.csv", index=False)

    # ---------------- permutation importance (model trained to 2020, tested 2021+)
    te = data[data["date"] >= "2021-01-01"].dropna(subset=["fwd20_open"])
    models = fitted[2021]

    def ic_of(df):
        s = P.score(models, df)
        s = s[s.groupby("date")["score"].transform("size") >= 10]
        return s.groupby("date").apply(lambda d: d["score"].corr(d["fwd20_open"], method="spearman")).mean()

    base_ic = ic_of(te)
    imp = {}
    for f in P.FEATS:
        sh = te.copy()
        sh[f] = sh.groupby("date")[f].transform(lambda x: x.sample(frac=1, random_state=7).values)
        imp[f] = float(base_ic - ic_of(sh))
    imp = pd.Series(imp).sort_values(ascending=False)
    res["importance"] = {"base_ic": float(base_ic), "drop": {k: round(v, 4) for k, v in imp.items()}}
    imp.head(12).round(4).rename_axis("feature").reset_index(name="drop").to_csv(f"{OUT}/importance.csv", index=False)
    print("importance done", flush=True)

    # ---------------- curves + yearly
    wk = strat.resample("W").last()
    pd.DataFrame({"date": wk.index.strftime("%Y-%m-%d"), "model": wk.values.round(4),
                  "market": bench.resample("W").last().reindex(wk.index).ffill().values.round(4),
                  "nofilter": nofilt.resample("W").last().reindex(wk.index).ffill().values.round(4),
                  "dd": (wk / wk.cummax() - 1).values.round(4)}).to_csv(f"{OUT}/curve.csv", index=False)
    rows = []
    for y in years:
        s, b = strat[strat.index.year == y], bench[bench.index.year == y]
        if s.empty:
            continue
        s0 = strat[strat.index < s.index[0]].iloc[-1] if (strat.index < s.index[0]).any() else 1.0
        b0 = bench[bench.index < b.index[0]].iloc[-1] if (bench.index < b.index[0]).any() else 1.0
        rows.append({"year": y, "model": round((s.iloc[-1] / s0 - 1) * 100, 2), "market": round((b.iloc[-1] / b0 - 1) * 100, 2)})
    pd.DataFrame(rows).to_csv(f"{OUT}/yearly.csv", index=False)
    res["yearly"] = rows

    with open(os.path.join(OUT, "results.json"), "w") as f:
        json.dump(res, f, indent=1, default=float)
    print(json.dumps({k: res[k] for k in ["headline", "ic", "deciles", "random", "excess_bootstrap"]}, indent=1, default=float))


if __name__ == "__main__":
    main()
