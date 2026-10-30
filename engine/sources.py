"""Data sources. Each source is optional; the app keeps working if one fails.

1. GitHub  - Aabishkar2/nepse-data: full daily history since 2011 for ~370 companies.
2. NEPSE   - nepalstock.com.np official API (via nepse-client): today's prices,
             NEPSE index, sectors, per-company history to fill gaps.
3. ShareSansar - today-share-price page: backup for today's prices.
"""
from __future__ import annotations

import io
import json
import logging
import os
import zipfile
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

log = logging.getLogger("sources")
NPT = timezone(timedelta(hours=5, minutes=45))
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
GITHUB_ZIP = "https://codeload.github.com/Aabishkar2/nepse-data/zip/refs/heads/main"
HEADER = "published_date,open,high,low,close,per_change,traded_quantity,traded_amount,status"


def now_npt() -> datetime:
    return datetime.now(NPT)


# ---------------------------------------------------------------- GitHub
def fetch_github_history(price_dir: str) -> dict:
    """Download the full dataset zip and merge into price_dir (never loses local rows)."""
    os.makedirs(price_dir, exist_ok=True)
    r = requests.get(GITHUB_ZIP, timeout=180, headers=UA)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    n_new = n_upd = 0
    for name in z.namelist():
        if "/data/company-wise/" not in name or not name.endswith(".csv"):
            continue
        sym = os.path.basename(name)[:-4].upper()
        if not sym.replace("_", "").isalnum():
            continue
        remote = pd.read_csv(io.BytesIO(z.read(name)))
        path = os.path.join(price_dir, f"{sym}.csv")
        if os.path.exists(path):
            local = pd.read_csv(path)
            merged = pd.concat([remote, local]).drop_duplicates("published_date", keep="last")
            if len(merged) != len(local):
                n_upd += 1
        else:
            merged = remote
            n_new += 1
        merged.sort_values("published_date").to_csv(path, index=False)
    return {"source": "github", "ok": True, "new": n_new, "updated": n_upd}


def _append_rows(price_dir: str, rows: list[dict], date: str) -> int:
    """rows: dicts with symbol, open, high, low, close, per_change, volume, turnover."""
    n = 0
    for r in rows:
        sym = str(r["symbol"]).upper().strip()
        path = os.path.join(price_dir, f"{sym}.csv")
        if not os.path.exists(path) or not r.get("close"):
            continue
        d = pd.read_csv(path)
        if str(d["published_date"].iloc[-1]) >= date:
            continue
        o, c = float(r.get("open") or r["close"]), float(r["close"])
        new = {
            "published_date": date, "open": o, "high": float(r.get("high") or max(o, c)),
            "low": float(r.get("low") or min(o, c)), "close": c,
            "per_change": float(r.get("per_change") or 0), "traded_quantity": float(r.get("volume") or 0),
            "traded_amount": float(r.get("turnover") or 0), "status": 1 if c > o else (-1 if o > c else 0),
        }
        pd.concat([d, pd.DataFrame([new])]).to_csv(path, index=False)
        n += 1
    return n


# ---------------------------------------------------------------- NEPSE official
def _nepse_client():
    from nepse_client import NepseClient  # optional dependency (Python >= 3.11)
    c = NepseClient()
    try:
        c.setTLSVerification(False)  # nepalstock.com.np often has certificate problems
    except Exception:
        pass
    return c


def _pick(d: dict, *keys):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def fetch_nepse_today(price_dir: str, meta_dir: str) -> dict:
    c = _nepse_client()
    status = c.getMarketStatus() or {}
    is_open = str(status.get("isOpen", "")).upper() == "OPEN"
    as_of = str(status.get("asOf") or now_npt().isoformat())[:10]
    pv = c.getPriceVolume() or []
    rows = []
    live = []
    for x in pv:
        row = {
            "symbol": _pick(x, "symbol"),
            "open": _pick(x, "openPrice"),
            "high": _pick(x, "highPrice"),
            "low": _pick(x, "lowPrice"),
            "close": _pick(x, "closePrice", "lastTradedPrice"),
            "per_change": _pick(x, "percentageChange"),
            "volume": _pick(x, "totalTradeQuantity", "totalTradedQuantity"),
            "turnover": _pick(x, "totalTradeValue", "totalTradedValue", "turnover"),
            "prev_close": _pick(x, "previousClose", "previousDayClosePrice"),
            "name": _pick(x, "securityName"),
        }
        if row["symbol"] and row["close"]:
            rows.append(row)
            live.append(row)
    os.makedirs(meta_dir, exist_ok=True)
    with open(os.path.join(meta_dir, "live.json"), "w") as f:
        json.dump({"as_of": now_npt().isoformat(), "market_open": is_open, "rows": live}, f)
    appended = 0 if is_open else _append_rows(price_dir, rows, as_of)
    # NEPSE index + sectors (cached)
    try:
        idx = c.getNepseIndex()
        with open(os.path.join(meta_dir, "nepse_index.json"), "w") as f:
            json.dump({"as_of": now_npt().isoformat(), "data": idx}, f)
    except Exception as e:  # noqa
        log.warning("index failed: %s", e)
    sec_path = os.path.join(meta_dir, "sectors.json")
    if not os.path.exists(sec_path) or (datetime.now().timestamp() - os.path.getmtime(sec_path)) > 7 * 86400:
        try:
            ss = c.getSectorScrips()
            sym2sec = {s: sec for sec, syms in ss.items() for s in syms}
            names = {x["symbol"]: x.get("companyName") or x.get("securityName") for x in c.getCompanyList()}
            with open(sec_path, "w") as f:
                json.dump({"sector": sym2sec, "name": names}, f)
        except Exception as e:  # noqa
            log.warning("sectors failed: %s", e)
    return {"source": "nepse", "ok": True, "market_open": is_open, "date": as_of, "rows": len(rows), "appended": appended}


def fill_gaps_from_nepse(price_dir: str, max_symbols=60) -> dict:
    """For symbols whose local file is behind, pull recent history from NEPSE."""
    c = _nepse_client()
    files = sorted(f for f in os.listdir(price_dir) if f.endswith(".csv"))
    lasts = {}
    for f in files:
        try:
            lasts[f[:-4]] = str(pd.read_csv(os.path.join(price_dir, f), usecols=["published_date"]).iloc[-1, 0])
        except Exception:
            pass
    if not lasts:
        return {"source": "nepse-gaps", "ok": True, "filled": 0}
    newest = max(lasts.values())
    cutoff = (pd.Timestamp(newest) - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
    behind = [s for s, d in lasts.items() if cutoff < d < newest][:max_symbols]
    filled = 0
    for s in behind:
        try:
            hist = c.getCompanyPriceVolumeHistory(s, start_date=lasts[s])
            items = hist.get("content", hist) if isinstance(hist, dict) else hist
            rows_by_date = {}
            for x in items or []:
                dt = str(_pick(x, "businessDate", "date") or "")[:10]
                if dt and dt > lasts[s]:
                    rows_by_date[dt] = {
                        "symbol": s, "open": _pick(x, "openPrice"), "high": _pick(x, "highPrice"),
                        "low": _pick(x, "lowPrice"), "close": _pick(x, "closePrice"),
                        "per_change": None, "volume": _pick(x, "totalTradedQuantity", "totalTradeQuantity"),
                        "turnover": _pick(x, "totalTradedValue", "totalTradeValue"),
                    }
            for dt in sorted(rows_by_date):
                filled += _append_rows(price_dir, [rows_by_date[dt]], dt)
        except Exception as e:  # noqa
            log.info("gap fill %s failed: %s", s, e)
    return {"source": "nepse-gaps", "ok": True, "checked": len(behind), "filled": filled}


# ---------------------------------------------------------------- ShareSansar
def fetch_sharesansar_today(price_dir: str) -> dict:
    from bs4 import BeautifulSoup

    r = requests.get("https://www.sharesansar.com/today-share-price", headers=UA, timeout=60)
    r.raise_for_status()
    bs = BeautifulSoup(r.text, "html.parser")
    span = bs.find("span", {"class": "text-org"})
    date = span.text.strip()[:10] if span else None
    tables = pd.read_html(io.StringIO(r.text))
    t = tables[0]
    rows = []
    for _, x in t.iterrows():
        try:
            rows.append({
                "symbol": x["Symbol"], "open": x.get("Open"), "high": x.get("High"), "low": x.get("Low"),
                "close": x.get("Close"), "per_change": x.get("Diff %"), "volume": x.get("Vol"),
                "turnover": x.get("Turnover"),
            })
        except Exception:
            continue
    # only append once the market has closed for that date
    n = 0
    if date and (date < now_npt().strftime("%Y-%m-%d") or now_npt().hour >= 15):
        n = _append_rows(price_dir, rows, date)
    return {"source": "sharesansar", "ok": True, "date": date, "rows": len(rows), "appended": n}


def update_all(price_dir: str, meta_dir: str, skip_github=False) -> list[dict]:
    results = []
    steps = [] if skip_github else [("github", lambda: fetch_github_history(price_dir))]
    steps += [
        ("nepse", lambda: fetch_nepse_today(price_dir, meta_dir)),
        ("nepse-gaps", lambda: fill_gaps_from_nepse(price_dir)),
        ("sharesansar", lambda: fetch_sharesansar_today(price_dir)),
    ]
    for name, fn in steps:
        try:
            res = fn()
        except Exception as e:  # noqa
            res = {"source": name, "ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
        log.info("source %s -> %s", name, res)
        results.append(res)
    return results
