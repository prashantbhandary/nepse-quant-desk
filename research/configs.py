"""Sensitivity of the walk-forward result to the three tree settings. Writes research/out/configs.json."""
import json
import os

import pandas as pd

from engine import pipeline as P
from research.reproduce import OUT, ROOT, period

p = P.Paths(ROOT)
panel, mkt, _, _ = P.build_panel(p)
data = panel[panel["tradable"]]
R = panel.pivot_table(index="date", columns="symbol", values="adj_open").sort_index()
C = panel.pivot_table(index="date", columns="symbol", values="adj_close").sort_index()
G = (R / C.ffill().shift(1) - 1).clip(-0.16, 0.16).fillna(0)
I = (C / R - 1).clip(-0.16, 0.16).fillna(0)
out = {}
for name in P.CONFIGS:
    P.ACTIVE["config"] = name
    preds = []
    for y in range(2016, int(panel["date"].dt.year.max()) + 1):
        cut = pd.Timestamp(f"{y}-01-01") - pd.Timedelta(days=40)
        tr, te = data[data["date"] < cut], data[data["date"].dt.year == y]
        if te.empty or len(tr) < 10000:
            continue
        preds.append(P.score(P.fit_ensemble(tr), te)[["date", "symbol", "score", "fwd20_open"]])
    pr = pd.concat(preds)
    ok = pr.dropna(subset=["fwd20_open"])
    ok = ok[ok.groupby("date")["score"].transform("size") >= 10]
    ic = ok.groupby("date").apply(lambda d: d["score"].corr(d["fwd20_open"], method="spearman")).mean()
    c, _ = P.simulate(pr, G, I, mkt["breadth50"])
    out[name] = {"ic": float(ic), **{f"{a[:4]}-{b[:4]}": period(c, a, b) for a, b in
                 [("2016-01-01", "2020-12-31"), ("2021-01-01", "2026-12-31"), ("2016-01-01", "2026-12-31")]}}
    print(name, out[name], flush=True)
json.dump(out, open(os.path.join(OUT, "configs.json"), "w"), indent=1)
