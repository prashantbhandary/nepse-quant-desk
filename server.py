"""Personal NEPSE trading assistant — local web server.

Run:  ./start.sh   (or: python3 server.py)   then open http://localhost:8050
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta

# Logging comes first so there is visible output while the heavy libraries load below.
ROOT = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(),
                              logging.FileHandler(os.path.join(ROOT, "data", "app.log"))])
logging.getLogger("werkzeug").setLevel(logging.WARNING)  # no line per request (the UI polls /api/status)
logging.getLogger("httpx").setLevel(logging.WARNING)  # nepse-client logs every API call otherwise
log = logging.getLogger("server")


def _log_crash(kind, value, tb):
    if not issubclass(kind, KeyboardInterrupt):
        log.critical("server crashed", exc_info=(kind, value, tb))
    sys.__excepthook__(kind, value, tb)


sys.excepthook = _log_crash  # so start-up crashes (e.g. a broken install) end up in data/app.log
log.info("Loading libraries… (numpy, pandas, scikit-learn — the first launch can take 1–2 minutes)")

from flask import Flask, abort, jsonify, request, send_from_directory  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from engine import pipeline  # noqa: E402
from engine.model import CGT_LONG, CGT_SHORT, DP_FEE, SEBON, broker_rate  # noqa: E402
from engine.sources import fetch_nepse_today, now_npt  # noqa: E402

P = pipeline.Paths(ROOT)
DB = os.path.join(ROOT, "data", "portfolio.db")
PORT = int(os.environ.get("PORT", "8050"))
app = Flask(__name__, static_folder=None)

STATE = {"running": False, "step": "", "last_error": None, "started": None, "live_at": None}
LOCK = threading.Lock()


class StepFromLog(logging.Handler):
    """Turn the pipeline's own log lines into a short progress step for the web UI."""
    STEPS = [
        ("source github ", "Fetching today's prices from NEPSE…"),
        ("source nepse ", "Filling missing days from NEPSE…"),
        ("source nepse-gaps ", "Checking ShareSansar for today's prices…"),
        ("source sharesansar ", "Calculating indicators for every stock…"),
        ("training models", "Training the model (2–3 min)…"),
        ("running walk-forward backtest", "Running the backtest…"),
    ]

    def emit(self, record):
        if not STATE["running"]:
            return
        msg = record.getMessage()
        for prefix, step in self.STEPS:
            if msg.startswith(prefix):
                STATE["step"] = step
                return


for _name in ("pipeline", "sources"):
    logging.getLogger(_name).addHandler(StepFromLog())


# ------------------------------------------------------------------ db
def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS transactions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, symbol TEXT, side TEXT,
            qty REAL, price REAL, notes TEXT, kind TEXT DEFAULT 'secondary');
        CREATE TABLE IF NOT EXISTS watchlist(symbol TEXT PRIMARY KEY, added TEXT, note TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS snapshots(date TEXT PRIMARY KEY, value REAL, cost REAL, realized REAL);
        CREATE TABLE IF NOT EXISTS journal(date TEXT PRIMARY KEY, note TEXT);
        """)
        if "pnl" not in [r[1] for r in c.execute("PRAGMA table_info(snapshots)")]:
            # total profit (closed + open) at each day's close; older rows fall back to value - cost + realized
            c.execute("ALTER TABLE snapshots ADD COLUMN pnl REAL")


DEFAULT_SETTINGS = {"capital": "500000", "max_positions": "3", "stop_pct": "10", "dp_fee": str(DP_FEE),
                    "monthly_target": "20000"}


def get_settings():
    with db() as c:
        rows = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM settings")}
    return {**DEFAULT_SETTINGS, **rows}


# ------------------------------------------------------------------ data helpers
def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def live_prices():
    lv = read_json(os.path.join(P.meta, "live.json"), {}) or {}
    rows = {r["symbol"]: r for r in lv.get("rows", []) if r.get("symbol")}
    return lv, rows


def price_map():
    stocks = read_json(os.path.join(P.out, "stocks.json"), []) or []
    m = {s["symbol"]: s for s in stocks}
    lv, live = live_prices()
    fresh = lv.get("as_of") and (datetime.now().astimezone() - datetime.fromisoformat(lv["as_of"])).total_seconds() < 6 * 3600
    return m, (live if fresh else {}), lv


# ------------------------------------------------------------------ fees
def commission(amt):
    return max(amt * broker_rate(amt), 10.0) if amt else 0.0  # brokers charge at least Rs 10 per trade


def buy_cost(qty, price, kind="secondary", dp=DP_FEE):
    amt = qty * price
    if kind != "secondary":  # IPO / right / bonus / MeroShare WACC (fees already in) / unknown -> nothing added
        return amt
    return amt + commission(amt) + amt * SEBON + dp


def sell_net(qty, price, cost_basis, held_days, dp=DP_FEE):
    amt = qty * price
    fees = commission(amt) + amt * SEBON + dp
    profit = amt - fees - cost_basis
    tax = max(profit, 0) * (CGT_LONG if held_days > 365 else CGT_SHORT)
    return amt - fees - tax, fees, tax


def compute_portfolio():
    s = get_settings()
    dp = float(s["dp_fee"])
    with db() as c:
        tx = [dict(r) for r in c.execute("SELECT * FROM transactions ORDER BY date, id")]
    lots = {}
    realized = 0.0
    invested_ever = 0.0  # every rupee spent on buys, including shares since sold
    realized_list = []
    for t in tx:
        sym = t["symbol"].upper()
        L = lots.setdefault(sym, {"qty": 0.0, "cost": 0.0, "first": t["date"]})
        if t["side"] == "BUY":
            if L["qty"] <= 0:
                L["first"] = t["date"]
            L["qty"] += t["qty"]
            L["unknown"] = L.get("unknown") or t.get("kind") == "unknown"  # buy price not entered yet
            paid = buy_cost(t["qty"], t["price"], t.get("kind") or "secondary", dp)
            L["cost"] += paid
            invested_ever += paid
        else:
            if L["qty"] <= 0:
                continue
            q = min(t["qty"], L["qty"])
            basis = L["cost"] * q / L["qty"]
            held = (datetime.fromisoformat(t["date"]) - datetime.fromisoformat(L["first"])).days
            net, fees, tax = sell_net(q, t["price"], basis, held, dp)
            realized += net - basis
            realized_list.append({"date": t["date"], "symbol": sym, "qty": q, "price": t["price"],
                                  "pnl": round(net - basis, 2), "pct": round((net / basis - 1) * 100, 2) if basis else 0})
            L["qty"] -= q
            L["cost"] -= basis
    pm, live, lv = price_map()
    stop_pct = float(s["stop_pct"]) / 100
    holdings = []
    tot_val = tot_cost = day_chg = 0.0
    known_val = known_cost = 0.0  # P&L totals only cover holdings whose buy price is known
    n_unknown = 0
    for sym, L in lots.items():
        if L["qty"] <= 1e-9:
            continue
        info = pm.get(sym, {})
        ltp = (live.get(sym) or {}).get("close") or info.get("price")
        prev_chg = (live.get(sym) or {}).get("per_change") if sym in live else info.get("chg1")
        unknown = L.get("unknown", False)
        wacc = L["cost"] / L["qty"]
        held = (datetime.now() - datetime.fromisoformat(L["first"])).days
        value = (ltp or 0) * L["qty"]
        # unknown buy price: no profit known, so no tax; only the selling fees apply
        net, fees, tax = sell_net(L["qty"], ltp or 0, value if unknown else L["cost"], held, dp) if ltp else (0, 0, 0)
        alerts = []
        action = info.get("action", "—")
        if not unknown and ltp and ltp < wacc * (1 - stop_pct):
            alerts.append(f"Down {((ltp / wacc) - 1) * 100:.1f}% from your cost — below your {s['stop_pct']}% stop-loss. Consider selling.")
        if info.get("stop") and ltp and ltp < info["stop"]:
            alerts.append("Price under the model's volatility stop.")
        if action == "SELL":
            alerts.append("Model says SELL: " + info.get("action_reason", ""))
        if not unknown and held < 365 and net - L["cost"] > 0 and 300 < held:
            alerts.append(f"Held {held} days — waiting until day 366 lowers tax from 7.5% to 5%.")
        h = {"symbol": sym, "name": info.get("name", ""), "sector": info.get("sector", ""), "qty": L["qty"],
             "wacc": round(wacc, 2), "cost": round(L["cost"], 2), "ltp": ltp, "value": round(value, 2),
             "day_chg_pct": prev_chg, "pnl_gross": round(value - L["cost"], 2),
             "pnl_net": round(net - L["cost"], 2), "pnl_net_pct": round((net / L["cost"] - 1) * 100, 2) if L["cost"] else 0,
             "sell_fees": round(fees, 2), "tax": round(tax, 2), "held_days": held, "since": L["first"],
             "action": action, "score": info.get("score"), "rank": info.get("rank"),
             "stop": info.get("stop"), "target": info.get("target"), "alerts": alerts, "price_unknown": unknown}
        if unknown:
            h.update(wacc=None, cost=None, pnl_gross=None, pnl_net=None, pnl_net_pct=None, held_days=None, since=None)
            n_unknown += 1
        else:
            known_val += value
            known_cost += L["cost"]
        holdings.append(h)
        tot_val += value
        tot_cost += L["cost"]
        if prev_chg is not None and ltp:
            day_chg += value - value / (1 + prev_chg / 100)
    holdings.sort(key=lambda x: -x["value"])
    for h in holdings:
        h["weight"] = round(h["value"] / tot_val * 100, 1) if tot_val else 0
    unreal = sum(h["pnl_net"] for h in holdings if not h["price_unknown"])
    with db() as c:
        snaps = [dict(r) for r in c.execute("SELECT * FROM snapshots ORDER BY date")]
    return {
        "holdings": holdings, "transactions": tx[::-1], "realized": realized_list[::-1],
        "totals": {"value": round(tot_val, 2), "cost": round(known_cost, 2), "pnl_gross": round(known_val - known_cost, 2),
                   "pnl_net": round(unreal, 2),
                   "unknown": n_unknown, "realized": round(realized, 2),
                   "invested_ever": round(invested_ever, 2), "total_pnl": round(realized + unreal, 2),
                   "total_pnl_pct": round((realized + unreal) / invested_ever * 100, 2) if invested_ever else 0,
                   "day_change": round(day_chg, 2),
                   "capital": float(s["capital"]), "cash_est": round(float(s["capital"]) - tot_cost + realized, 2)},
        "snapshots": snaps, "live_as_of": lv.get("as_of"), "market_open": lv.get("market_open"), "settings": s,
    }


def snapshot_portfolio(date):
    try:
        pf = compute_portfolio()
        t = pf["totals"]
        with db() as c:
            c.execute("INSERT OR REPLACE INTO snapshots(date, value, cost, realized, pnl) VALUES(?,?,?,?,?)",
                      (date, t["value"], t["cost"], t["realized"], t["total_pnl"]))
    except Exception as e:  # noqa
        log.warning("snapshot failed: %s", e)


# ------------------------------------------------------------------ background jobs
def run_update(full=True, retrain=False):
    if not LOCK.acquire(blocking=False):
        return False

    def job():
        st = read_json(os.path.join(P.out, "status.json"), {}) or {}
        same_day = str(st.get("last_run", ""))[:10] == datetime.now().strftime("%Y-%m-%d")
        first = "Fetching today's prices from NEPSE…" if same_day and not retrain else \
            "Downloading price history from GitHub (15 years, ~1 min)…"
        STATE.update(running=True, step=first if full else "Running analysis…",
                     started=datetime.now().isoformat(), last_error=None)
        try:
            res = pipeline.run(ROOT, fetch=full, retrain=retrain, skip_github=same_day and not retrain)
            snapshot_portfolio(res["as_of"])
            STATE["step"] = "Done"
        except Exception as e:  # noqa
            log.exception("update failed")
            STATE["last_error"] = f"{type(e).__name__}: {e}"
        finally:
            STATE["running"] = False
            LOCK.release()

    STATE["running"] = True  # set before the thread starts so /api/status never reports a gap
    threading.Thread(target=job, daemon=True).start()
    return True


def refresh_live():
    try:
        fetch_nepse_today(P.prices, P.meta)
        STATE["live_at"] = datetime.now().isoformat()
    except Exception as e:  # noqa
        log.info("live refresh failed: %s", e)


def is_trading_day(d):
    return d.weekday() in (6, 0, 1, 2, 3)  # Sunday–Thursday


def scheduler():
    time.sleep(3)
    last_live = 0
    last_try = 0
    while True:
        try:
            n = now_npt()
            st = read_json(os.path.join(P.out, "status.json"), {}) or {}
            last_run = st.get("last_run")
            today = n.strftime("%Y-%m-%d")
            # after market close: one full update per trading day
            need = False
            if not last_run:
                need = True
            elif is_trading_day(n) and (n.hour, n.minute) >= (15, 20) and last_run[:10] < today:
                need = True
            elif (datetime.now() - datetime.fromisoformat(last_run)) > timedelta(hours=30):
                need = True
            if need and not STATE["running"] and time.time() - last_try > 1800:
                last_try = time.time()
                log.info("scheduled update starting")
                run_update(full=True)
            # live prices during market hours
            if is_trading_day(n) and 11 <= n.hour < 15 and time.time() - last_live > 180 and not STATE["running"]:
                last_live = time.time()
                threading.Thread(target=refresh_live, daemon=True).start()
        except Exception as e:  # noqa
            log.warning("scheduler: %s", e)
        time.sleep(60)


# ------------------------------------------------------------------ routes
@app.get("/")
def index():
    return send_from_directory(os.path.join(ROOT, "web"), "index.html", max_age=0)


@app.get("/web/<path:f>")
def web(f):
    return send_from_directory(os.path.join(ROOT, "web"), f, max_age=0)  # always revalidate, so edits show on reload


@app.get("/api/summary")
def api_summary():
    return jsonify(read_json(os.path.join(P.out, "summary.json"), {}))


@app.get("/api/stocks")
def api_stocks():
    rows = read_json(os.path.join(P.out, "stocks.json"), []) or []
    _, live, lv = price_map()
    with db() as c:
        wl = {r["symbol"] for r in c.execute("SELECT symbol FROM watchlist")}
    for r in rows:
        r["watch"] = r["symbol"] in wl
        if r["symbol"] in live and lv.get("market_open"):
            r["live"] = live[r["symbol"]].get("close")
            r["live_chg"] = live[r["symbol"]].get("per_change")
    return jsonify(rows)


@app.get("/api/stock/<sym>")
def api_stock(sym):
    sym = "".join(ch for ch in sym.upper() if ch.isalnum())
    d = read_json(os.path.join(P.stock, f"{sym}.json"))
    if not d:
        abort(404)
    rows = read_json(os.path.join(P.out, "stocks.json"), []) or []
    d["signal"] = next((r for r in rows if r["symbol"] == sym), None)
    with db() as c:
        d["transactions"] = [dict(r) for r in c.execute("SELECT * FROM transactions WHERE symbol=? ORDER BY date", (sym,))]
        d["watch"] = bool(c.execute("SELECT 1 FROM watchlist WHERE symbol=?", (sym,)).fetchone())
    return jsonify(d)


@app.get("/api/backtest")
def api_backtest():
    return jsonify(read_json(os.path.join(P.out, "backtest.json"), {}))


@app.get("/api/model_history")
def api_model_history():
    return jsonify(read_json(os.path.join(P.out, "model_history.json"), []) or [])


@app.get("/api/records")
def api_records():
    r = read_json(os.path.join(P.out, "records.json"), {"records": []})
    with db() as c:
        notes = {x["date"]: x["note"] for x in c.execute("SELECT * FROM journal")}
        snaps = {x["date"]: dict(x) for x in c.execute("SELECT * FROM snapshots")}
    for rec in r.get("records", []):
        rec["note"] = notes.get(rec["date"], "")
        rec["portfolio"] = snaps.get(rec["date"])
    return jsonify(r)


@app.post("/api/journal")
def api_journal():
    j = request.get_json(force=True)
    with db() as c:
        c.execute("INSERT OR REPLACE INTO journal(date, note) VALUES(?,?)", (j["date"], j.get("note", "")))
    return jsonify(ok=True)


@app.get("/api/status")
def api_status():
    st = read_json(os.path.join(P.out, "status.json"), {}) or {}
    lv = read_json(os.path.join(P.meta, "live.json"), {}) or {}
    model = None
    try:
        import joblib
        info = joblib.load(os.path.join(P.models, "ensemble.joblib"))
        model = {k: info[k] for k in ("trained", "train_until", "rows")}
    except Exception:
        pass
    return jsonify({**st, "running": STATE["running"], "step": STATE["step"], "error": STATE["last_error"],
                    "started": STATE["started"],
                    "live_as_of": lv.get("as_of"), "market_open": lv.get("market_open"), "model": model,
                    "now_npt": now_npt().isoformat(timespec="minutes")})


@app.post("/api/update")
def api_update():
    j = request.get_json(silent=True) or {}
    ok = run_update(full=True, retrain=bool(j.get("retrain")))
    return jsonify(started=ok, running=True)


@app.post("/api/live")
def api_live():
    threading.Thread(target=refresh_live, daemon=True).start()
    return jsonify(ok=True)


@app.get("/api/portfolio")
def api_portfolio():
    return jsonify(compute_portfolio())


def snap_paper(x):
    """Profit on shares still held at that day's close (total profit minus what was already sold)."""
    total = x["pnl"] if x.get("pnl") is not None else x["value"] - x["cost"] + x["realized"]
    return total - x["realized"]


@app.get("/api/profit")
def api_profit():
    """Profit month by month against the monthly target, after fees and tax. A month's profit = profit
    booked by sells dated in that month + change in on-paper profit of held shares over the month."""
    pf = compute_portfolio()
    t = pf["totals"]
    target = float(pf["settings"]["monthly_target"] or 0)
    snaps = pf["snapshots"]
    now = datetime.now()
    this_month = now.strftime("%Y-%m")
    points = [(x["date"], snap_paper(x)) for x in snaps] + [(now.strftime("%Y-%m-%d"), t["pnl_net"])]
    first = points[0][0][:7]
    months = []
    # months with a sell before tracking began count only what was booked; tracked months add the paper change
    for m in sorted({d[:7] for d, _ in points} | {r["date"][:7] for r in pf["realized"]}):
        booked = sum(r["pnl"] for r in pf["realized"] if r["date"][:7] == m)
        paper = 0.0
        if m >= first:
            before = [v for d, v in points if d[:7] < m]
            inside = [v for d, v in points if d[:7] == m]
            start = before[-1] if before else inside[0]  # first tracked month starts from the first snapshot
            paper = inside[-1] - start
        profit = booked + paper
        months.append({"month": m, "profit": round(profit, 2), "booked": round(booked, 2),
                       "paper": round(paper, 2), "hit": profit >= target,
                       "partial": m == first, "sells_only": m < first})
    cur = months[-1] if months and months[-1]["month"] == this_month else {"profit": 0, "booked": 0, "paper": 0}
    # trading days (Sun–Thu) left this month, today included while the market can still open
    d, left = now, 0
    while d.strftime("%Y-%m") == this_month:
        left += is_trading_day(d) and (d.date() > now.date() or d.hour < 15)
        d += timedelta(days=1)
    bt = read_json(os.path.join(P.out, "backtest.json"), {}) or {}
    # cautious: the strategy's compound yearly return over the whole 10-year test, not its best recent years
    yearly = min(max((bt.get("strategy") or {}).get("cagr") or 0.10, 0.05), 0.25)
    return jsonify({
        "target": target, "month": this_month, "current": cur, "months": months[::-1],
        "remaining": round(max(target - cur["profit"], 0), 2), "trading_days_left": left,
        "per_day_needed": round(max(target - cur["profit"], 0) / left, 2) if left else None,
        "totals": t, "realized": pf["realized"],
        "realistic": {"yearly_pct": round(yearly * 100, 1),
                      "monthly_on_value": round(t["value"] * yearly / 12, 0),
                      "capital_needed": round(target * 12 / yearly, -3) if target else None},
        "tracking_since": snaps[0]["date"] if snaps else None,
    })


def tx_row(j):
    """Validated (date, symbol, side, qty, price, notes, kind) from a JSON body."""
    sym = "".join(ch for ch in str(j["symbol"]).upper() if ch.isalnum())
    side = "SELL" if str(j.get("side", "BUY")).upper() == "SELL" else "BUY"
    qty, price = float(j["qty"]), float(j["price"])
    if qty <= 0 or price < 0 or not sym or (j.get("kind", "secondary") == "secondary" and price == 0):
        abort(400)  # only bonus / unknown-price entries may have price 0
    return (j.get("date") or datetime.now().strftime("%Y-%m-%d"), sym, side, qty, price,
            j.get("notes", ""), j.get("kind", "secondary"))


@app.post("/api/transactions")
def api_add_tx():
    with db() as c:
        c.execute("INSERT INTO transactions(date, symbol, side, qty, price, notes, kind) VALUES(?,?,?,?,?,?,?)",
                  tx_row(request.get_json(force=True)))
    return jsonify(ok=True)


@app.put("/api/transactions/<int:tid>")
def api_edit_tx(tid):
    with db() as c:
        c.execute("UPDATE transactions SET date=?, symbol=?, side=?, qty=?, price=?, notes=?, kind=? WHERE id=?",
                  (*tx_row(request.get_json(force=True)), tid))
    return jsonify(ok=True)


@app.delete("/api/transactions/<int:tid>")
def api_del_tx(tid):
    with db() as c:
        c.execute("DELETE FROM transactions WHERE id=?", (tid,))
    return jsonify(ok=True)


@app.get("/api/watchlist")
def api_watchlist():
    with db() as c:
        return jsonify([dict(r) for r in c.execute("SELECT * FROM watchlist ORDER BY added DESC")])


@app.post("/api/watchlist")
def api_watch_add():
    j = request.get_json(force=True)
    sym = "".join(ch for ch in str(j["symbol"]).upper() if ch.isalnum())
    with db() as c:
        if j.get("remove"):
            c.execute("DELETE FROM watchlist WHERE symbol=?", (sym,))
        else:
            c.execute("INSERT OR IGNORE INTO watchlist(symbol, added, note) VALUES(?,?,?)",
                      (sym, datetime.now().strftime("%Y-%m-%d"), j.get("note", "")))
    return jsonify(ok=True)


@app.get("/api/settings")
def api_settings():
    return jsonify(get_settings())


@app.post("/api/settings")
def api_settings_save():
    j = request.get_json(force=True)
    with db() as c:
        for k, v in j.items():
            if k in DEFAULT_SETTINGS:
                c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", (k, str(v)))
    return jsonify(get_settings())


@app.post("/api/calc")
def api_calc():
    """Trade calculator: costs and break-even for a planned buy."""
    j = request.get_json(force=True)
    qty, price = float(j["qty"]), float(j["price"])
    sell = float(j.get("sell") or price)
    days = int(j.get("days") or 30)
    cost = buy_cost(qty, price)
    net, fees, tax = sell_net(qty, sell, cost, days)
    # break-even sell price
    lo, hi = price, price * 1.2
    for _ in range(50):
        mid = (lo + hi) / 2
        n2, _, _ = sell_net(qty, mid, cost, days)
        lo, hi = (mid, hi) if n2 < cost else (lo, mid)
    return jsonify({"buy_total": round(cost, 2), "buy_fees": round(cost - qty * price, 2), "sell_fees": round(fees, 2),
                    "tax": round(tax, 2), "net_receive": round(net, 2), "profit": round(net - cost, 2),
                    "profit_pct": round((net / cost - 1) * 100, 2), "breakeven": round(hi, 2)})


if __name__ == "__main__":
    init_db()
    try:
        srv = make_server("127.0.0.1", PORT, app, threaded=True)
    except OSError as e:
        log.error("Port %s is already in use (%s). Stop the other program or run: PORT=%s ./start.sh", PORT, e, PORT + 1)
        sys.exit(1)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    if not (read_json(os.path.join(P.out, "summary.json"), {}) or {}).get("as_of"):
        log.info("First run: downloading 15 years of data and training the model (3–5 min). "
                 "The dashboard fills in automatically.")
        run_update(full=True)
    threading.Thread(target=scheduler, daemon=True).start()
    log.info("Server ready: http://localhost:%s", PORT)
    try:
        srv.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        log.info("Server stopped")
