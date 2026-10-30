/* NEPSE Desk — personal trading assistant (frontend) */
const $ = (s, el = document) => el.querySelector(s);
const app = $("#app");
const LW = window.LightweightCharts;
let charts = [];

// ---------------------------------------------------------------- utils
const api = async (url, opts = {}) => {
  const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) throw new Error(url + " " + r.status);
  return r.json();
};
const post = (url, body) => api(url, { method: "POST", body: JSON.stringify(body || {}) });
const nf = (x, d = 2) => (x === null || x === undefined || isNaN(x)) ? "—" :
  Number(x).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
const rs = (x, d = 0) => (x === null || x === undefined) ? "—" : "Rs " + nf(x, d);
const pct = (x, d = 2) => (x === null || x === undefined) ? "—" :
  `<span class="${x > 0 ? "up" : x < 0 ? "down" : "muted"}">${x > 0 ? "+" : ""}${nf(x, d)}%</span>`;
const sgn = (x, d = 0) => (x === null || x === undefined) ? "—" :
  `<span class="${x > 0 ? "up" : x < 0 ? "down" : "muted"}">${x > 0 ? "+" : ""}${nf(x, d)}</span>`;
const badge = a => `<span class="badge b-${a}">${a}</span>`;
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const symlink = s => `<a class="sym" href="#/stock/${esc(s)}">${esc(s)}</a>`;
const toast = (msg, ms = 2600) => { const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden"); setTimeout(() => t.classList.add("hidden"), ms); };
const today = () => new Date().toISOString().slice(0, 10);
function killCharts() { charts.forEach(c => { try { c.remove(); } catch (e) { } }); charts = []; }
function mkChart(el, opts = {}) {
  const c = LW.createChart(el, {
    autoSize: true,
    layout: { background: { color: "transparent" }, textColor: "#64748b", fontSize: 11 },
    grid: { vertLines: { color: "#eef2f6" }, horzLines: { color: "#eef2f6" } },
    rightPriceScale: { borderColor: "#d6dde5" }, timeScale: { borderColor: "#d6dde5" },
    crosshair: { mode: 0 }, ...opts,
  });
  charts.push(c); return c;
}
const series = (dates, vals) => dates.map((d, i) => ({ time: d, value: vals[i] })).filter(x => x.value !== null && x.value !== undefined);
function modal(html) { $("#modalCard").innerHTML = html; $("#modal").classList.remove("hidden"); }
function closeModal() { $("#modal").classList.add("hidden"); }
$("#modal").addEventListener("click", e => { if (e.target.id === "modal") closeModal(); });

function sortable(table, rows, render) {
  let key = null, dir = 1;
  table.querySelectorAll("th[data-k]").forEach(th => th.addEventListener("click", () => {
    const k = th.dataset.k; dir = key === k ? -dir : (th.dataset.desc ? -1 : 1); key = k;
    rows.sort((a, b) => {
      const x = a[k], y = b[k];
      if (x === null || x === undefined) return 1; if (y === null || y === undefined) return -1;
      return (typeof x === "string" ? x.localeCompare(y) : x - y) * dir;
    });
    render();
  }));
}

// ---------------------------------------------------------------- status bar
let lastStatus = {};
async function refreshStatus() {
  try {
    const s = await api("/api/status"); lastStatus = s;
    const live = s.market_open ? `<span class="dot live"></span>Market open · live ${s.live_as_of ? s.live_as_of.slice(11, 16) : ""}` : `<span class="dot"></span>Market closed`;
    const run = s.running ? ` · <b>Updating…</b>` : "";
    const err = s.error ? ` · <span class="down" title="${esc(s.error)}">update error</span>` : "";
    $("#statusText").innerHTML = `${live} · Data as of <b>${s.as_of || "—"}</b>${run}${err}`;
    $("#btnUpdate").disabled = !!s.running;
    return s;
  } catch (e) { $("#statusText").textContent = "Server not reachable"; }
}
$("#btnUpdate").addEventListener("click", async () => {
  await post("/api/update"); toast("Fetching latest data and re-running the model… (1–3 min)");
  refreshStatus();
  const iv = setInterval(async () => { const s = await refreshStatus(); if (s && !s.running) { clearInterval(iv); toast("Updated"); route(); } }, 5000);
});
setInterval(refreshStatus, 60000);

// ---------------------------------------------------------------- router
const routes = { "": plan, profit, today: dash, stocks, stock, portfolio, records, model, guide };
async function route() {
  killCharts(); clearInterval(setupTimer);
  const h = location.hash.replace(/^#\/?/, "").split("/");
  const r = h[0] || "";
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("on", (a.dataset.r === (r || "plan")) || (r === "stock" && a.dataset.r === "stocks")));
  $("#more").classList.toggle("on", !!$("#more .menu a.on")); $("#more").open = false;
  app.innerHTML = `<div class="empty">Loading…</div>`;
  try { await (routes[r] || plan)(h[1]); } catch (e) {
    console.error(e);
    if (!lastStatus.as_of && (lastStatus.running || lastStatus.error)) setupScreen(lastStatus);  // first run still going
    else app.innerHTML = `<div class="card empty">Could not load: ${esc(e.message)}<br><br>If this is the first run, click <b>Update now</b> and wait 2–3 minutes.</div>`;
  }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);

// ---------------------------------------------------------------- first-run setup screen
let setupTimer = null;
const mmss = sec => `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
function setupScreen(st) {
  clearInterval(setupTimer);
  if (!st.running) {
    app.innerHTML = `<div class="card setup" role="alert">
      <h2>Setup stopped with an error</h2>
      <pre class="setup-err">${esc(st.error)}</pre>
      <p class="muted">More details are in <code>data/app.log</code>. Check your internet connection, then try again.</p>
      <button class="btn primary" id="setupRetry">Try again</button></div>`;
    $("#setupRetry").onclick = async () => { $("#setupRetry").disabled = true; await post("/api/update"); await refreshStatus(); route(); };
    return;
  }
  app.innerHTML = `<div class="card setup">
    <div class="spinner" aria-hidden="true"></div>
    <h2>Setting up: downloading data and training the model…</h2>
    <p aria-live="polite">Step: <b id="setupStep">${esc(st.step || "starting")}</b></p>
    <p class="muted">Elapsed <span id="setupTime" class="num">0:00</span> · the first run usually takes 3–5 minutes.<br>
    The dashboard loads by itself when it’s done. You can leave this page open.</p></div>`;
  const t0 = st.started ? Date.parse(st.started) : Date.now();
  const setText = (id, t) => { const el = $(id); if (el) el.textContent = t; };
  let tick = 0;
  setupTimer = setInterval(async () => {
    if (!$("#setupTime")) { clearInterval(setupTimer); return; }
    setText("#setupTime", mmss(Math.max(0, Math.round((Date.now() - t0) / 1000))));
    if (++tick % 5) return;  // re-check the server every 5 s
    const s = await refreshStatus();
    if (!s) setText("#setupStep", "server not reachable, retrying…");
    else if (s.running) setText("#setupStep", s.step || "working");
    else { clearInterval(setupTimer); if (!s.error) toast("Setup finished"); route(); }
  }, 1000);
}

// ---------------------------------------------------------------- dashboard
async function dash() {
  const [s, pf] = await Promise.all([api("/api/summary"), api("/api/portfolio").catch(() => null)]);
  if (!s.as_of) {
    const st = await refreshStatus();
    if (st && (st.running || st.error)) { setupScreen(st); return; }
    app.innerHTML =`<div class="card empty">No analysis yet. Click <b>Update now</b> (top right). First run downloads 15 years of data and trains the model — about 3 minutes.</div>`; return; }
  const m = s.market;
  const nextDay = "next trading day";
  const holdAlerts = (pf?.holdings || []).filter(h => h.alerts.length || h.action === "SELL");
  const off = m.official_index?.data;
  const offTxt = off ? (Array.isArray(off) ? off.find(x => /nepse/i.test(x.index || ""))?.close ?? "" : off.close ?? off.currentValue ?? "") : "";
  app.innerHTML = `
  <h1>Today’s plan</h1>
  <p class="sub">Based on market close of <b>${s.as_of}</b>. Act on the ${nextDay}. Generated ${s.generated.replace("T", " ")}.</p>
  <div class="banner ${m.risk_on ? "on" : "off"}">
    <div><div class="big"><span class="mood ${m.risk_on ? "on" : "off"}"></span>Market mood: ${m.mood} — ${m.risk_on ? "OK to buy the top picks" : "Do NOT open new positions"}</div>
    <div class="muted">${m.breadth50}% of liquid stocks are above their 50-day average (model buys only when above ${s.settings.breadth_risk_on}%). ${m.risk_on ? "" : "In past weak markets, staying in cash avoided most of the big losses."}</div></div>
  </div>
  <div class="grid g4" style="margin-bottom:16px">
    <div class="card kpi"><div class="l">Advances / Declines</div><div class="v"><span class="up">${m.advances}</span> / <span class="down">${m.declines}</span></div><div class="l">of ${m.stocks} liquid stocks</div></div>
    <div class="card kpi"><div class="l">Market (equal-weight) today</div><div class="v">${pct(m.proxy_chg1)}</div><div class="l">20 days: ${pct(m.proxy_chg20)}${offTxt ? ` · NEPSE ${nf(offTxt)}` : ""}</div></div>
    <div class="card kpi"><div class="l">Above 200-day average</div><div class="v">${nf(m.breadth200, 1)}%</div><div class="l">long-term health of the market</div></div>
    <div class="card kpi"><div class="l">My portfolio</div><div class="v">${pf ? rs(pf.totals.value) : "—"}</div><div class="l">${pf && pf.totals.cost ? `P&L after fees & tax: ${sgn(pf.totals.pnl_net)}` : `<a href="#/portfolio">Add your shares →</a>`}</div></div>
  </div>
  <div class="grid g21">
    <div class="card">
      <h2>${m.risk_on ? `Buy candidates (top ${s.settings.top_n})` : "Top-ranked stocks (no new buys while market is weak)"}</h2>
      ${(s.buys.length ? s.buys : s.watch.slice(0, 5)).map(pickCard).join("") || `<div class="empty">No picks today.</div>`}
    </div>
    <div class="grid" style="align-content:start">
      <div class="card"><h2>My shares that need action</h2>
        ${holdAlerts.length ? holdAlerts.map(h => `<div style="padding:8px 0;border-bottom:1px solid var(--line)">${symlink(h.symbol)} ${badge(h.action)} <span class="muted">${nf(h.qty, 0)} @ ${nf(h.wacc)}</span> → ${nf(h.ltp)} ${pct(h.pnl_net_pct)}${h.alerts.map(a => `<div class="alert">${esc(a)}</div>`).join("")}</div>`).join("") :
      `<div class="muted">${pf && pf.holdings.length ? "All holdings look fine. Keep holding." : "No holdings yet. Add them in My portfolio to get sell alerts."}</div>`}
      </div>
      <div class="card"><h2>Close to a buy</h2>
        <table><tbody>${s.watch.slice(0, 8).map(x => `<tr><td>${symlink(x.symbol)}</td><td>${nf(x.price)}</td><td>${pct(x.chg1)}</td><td class="muted">score ${nf(x.score, 0)}</td></tr>`).join("")}</tbody></table>
      </div>
    </div>
  </div>
  <div class="card" style="margin-top:16px"><h2>Market trend (equal-weight index of liquid stocks) &amp; breadth</h2><div id="mchart" class="chart"></div></div>
  <div class="grid g3" style="margin-top:16px">
    ${miniTable("Top gainers", s.gainers, "chg1")}
    ${miniTable("Top losers", s.losers, "chg1")}
    ${miniTable("Most traded (turnover)", s.top_turnover, "turnover")}
  </div>
  <p class="muted" style="margin-top:18px;font-size:12px">Sources: ${(s.sources || []).map(x => `${x.source}: ${x.ok ? "ok" : "failed"}`).join(" · ") || "local data"}. Signals are probabilities, not guarantees — see “Model &amp; results”.</p>`;
  const c = mkChart($("#mchart"));
  const ls = c.addLineSeries({ color: "#2563eb", lineWidth: 2, priceScaleId: "right" });
  ls.setData(series(m.series.dates, m.series.index));
  const bs = c.addHistogramSeries({ priceScaleId: "b", color: "rgba(5,150,105,.25)", priceFormat: { type: "percent" } });
  c.priceScale("b").applyOptions({ scaleMargins: { top: 0.75, bottom: 0 } });
  bs.setData(series(m.series.dates, m.series.breadth).map(x => ({ ...x, color: x.value > 40 ? "rgba(5,150,105,.3)" : "rgba(220,38,38,.3)" })));
  c.timeScale().fitContent();
}
function pickCard(x) {
  return `<div class="pick">
    <div>
      <div style="display:flex;gap:8px;align-items:center">${symlink(x.symbol)} ${badge(x.action)} <span class="muted">#${x.rank ?? "—"}</span></div>
      <div class="muted" style="font-size:12px">${esc(x.name || x.sector || "")}</div>
      <div style="font-size:20px;font-weight:700;margin-top:4px">Rs ${nf(x.price)} <span style="font-size:13px">${pct(x.chg1)}</span></div>
      <div class="muted" style="font-size:12px">Model score ${nf(x.score, 0)}/100 · beats market odds ${nf(x.prob_top20, 0)}% · expected vs market (20d) ${pct(x.exp_excess20)}</div>
      <div class="plan">
        <div><span>Buy up to</span><b>${nf(x.entry_max)}</b></div>
        <div><span>Stop-loss</span><b class="down">${nf(x.stop)}</b></div>
        <div><span>Target</span><b class="up">${nf(x.target)}</b></div>
        <div><span>RSI</span><b>${nf(x.rsi, 0)}</b></div>
      </div>
    </div>
    <ul class="reasons">${x.good.slice(0, 5).map(g => `<li class="g">${esc(g)}</li>`).join("")}${x.bad.slice(0, 3).map(b => `<li class="b">${esc(b)}</li>`).join("")}</ul>
  </div>`;
}
function miniTable(title, rows, k) {
  return `<div class="card"><h2>${title}</h2><table><tbody>${(rows || []).slice(0, 8).map(x =>
    `<tr><td>${symlink(x.symbol)}</td><td>${nf(x.price)}</td><td>${k === "turnover" ? "Rs " + nf(x.turnover / 1e5, 1) + " L" : pct(x.chg1)}</td><td>${badge(x.action)}</td></tr>`).join("")}</tbody></table></div>`;
}

// ---------------------------------------------------------------- tomorrow's plan (home)
function nextTradingDay(asOf) {  // NEPSE trades Sunday–Thursday (public holidays not known here)
  const d = new Date(asOf + "T12:00:00");
  do d.setDate(d.getDate() + 1); while (d.getDay() === 5 || d.getDay() === 6);
  return d.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "short" });
}
async function plan() {
  const [s, pf, bt, pr] = await Promise.all([api("/api/summary"), api("/api/portfolio").catch(() => null),
    api("/api/backtest").catch(() => ({})), api("/api/profit").catch(() => null)]);
  const ps = bt.pick_stats;
  if (!s.as_of) {
    const st = await refreshStatus();
    if (st && (st.running || st.error)) { setupScreen(st); return; }
    app.innerHTML = `<div class="card empty">No analysis yet. Click <b>Update now</b> (top right) and wait about 3 minutes.</div>`; return;
  }
  const m = s.market, set = pf?.settings || {}, holds = pf?.holdings || [];
  const isSell = h => h.action === "SELL" || (h.stop && h.ltp && h.ltp < h.stop) || h.alerts.some(a => /stop-loss/.test(a));
  const sells = holds.filter(isSell), keeps = holds.filter(h => !isSell(h));
  const owned = new Set(holds.map(h => h.symbol));
  const maxPos = +set.max_positions || 3, slots = Math.max(0, maxPos - keeps.length);
  const cash = Math.max(0, pf ? pf.totals.cash_est + sells.reduce((a, h) => a + h.value - h.sell_fees - h.tax, 0) : 0);
  const perPick = slots ? Math.min(cash / slots, (+set.capital || 0) / maxPos) : 0;
  const warn = x => [/D\d+$/.test(x.symbol) ? "Looks like a debenture (bond), not a share — skip it unless you want a bond." : "",
                     !x.volume ? "Did not trade today — may be hard to buy." : ""].filter(Boolean);
  let free = slots;  // only fill the free slots; flagged picks never take one
  const buyRow = x => {
    const kitta = Math.floor(perPick / (x.entry_max || x.price));
    const use = !owned.has(x.symbol) && !warn(x).length && kitta > 0 && free > 0 && free--;
    return `<div class="prow">
      <div class="pmain">${symlink(x.symbol)} <span class="muted">${esc(x.name || "")}</span>
        <div class="muted">Today Rs ${nf(x.price)} ${pct(x.chg1)} · model score ${nf(x.score, 0)}/100</div>
        ${warn(x).map(w => `<div class="alert warn">${esc(w)}</div>`).join("")}</div>
      <div class="pnums">
        ${owned.has(x.symbol) ? `<div class="pbig">You own it — keep</div>` :
          use ? `<div class="pbig up">Buy ${kitta} kitta</div><div class="muted">at Rs ${nf(x.entry_max)} or less · ≈ ${rs(kitta * x.entry_max)}</div>` :
          kitta > 0 && !warn(x).length ? `<div class="pbig muted">Backup</div><div class="muted">only if a pick above can’t be bought at its price</div>` :
          `<div class="pbig muted">Skip</div>`}
        <div class="muted">Sell if it falls to <b class="down">${nf(x.stop)}</b> · target <b class="up">${nf(x.target)}</b></div></div></div>`;
  };
  const holdRow = (h, sell) => `<div class="prow">
      <div class="pmain">${symlink(h.symbol)} ${badge(h.action)} <span class="muted">${nf(h.qty, 0)} kitta</span>
        <div class="muted">Now Rs ${nf(h.ltp)} · ${h.price_unknown ? `buy price not entered (<a href="#/portfolio">add it</a>)` : `cost ${nf(h.wacc)} · after fees &amp; tax ${sgn(h.pnl_net)} (${pct(h.pnl_net_pct)})`}</div>
        ${sell ? h.alerts.map(a => `<div class="alert">${esc(a)}</div>`).join("") : ""}</div>
      <div class="pnums">${sell ? `<div class="pbig down">Sell ${nf(h.qty, 0)} kitta</div><div class="muted">you receive ≈ ${rs(h.value - h.sell_fees - h.tax)}</div>` :
        `<div class="pbig">Keep</div><div class="muted">sell if it falls to <b class="down">${nf(h.stop)}</b>${h.target ? ` · target <b class="up">${nf(h.target)}</b>` : ""}</div>`}</div></div>`;
  app.innerHTML = `
  <h1>Plan for ${nextTradingDay(s.as_of)}</h1>
  <p class="sub">From the whole-market analysis of ${m.stocks} liquid stocks at the close of <b>${s.as_of}</b>, and your portfolio.</p>
  ${pr && pr.target ? `<a class="card target-strip" href="#/profit" style="color:inherit">
    <div><span class="muted">${monthName(pr.month)} profit</span> <b>${sgn(pr.current.profit)}</b> <span class="muted">of ${rs(pr.target)}</span></div>
    <div class="progress" aria-hidden="true"><i style="width:${Math.max(0, Math.min(100, pr.current.profit / pr.target * 100))}%"></i></div>
    <span class="muted">Details →</span></a>` : ""}
  <div class="banner ${m.risk_on ? "on" : "off"}"><div class="big"><span class="mood ${m.risk_on ? "on" : "off"}"></span>${m.risk_on ? "Market is healthy — OK to buy" : "Market is weak — do NOT buy anything new"}</div>
    <div class="muted">${nf(m.breadth50, 0)}% of stocks are above their 50-day average (buy only above ${s.settings.breadth_risk_on}%).</div></div>
  <div class="card"><h2>1 · Sell</h2>
    ${!holds.length ? `<div class="muted">Add your shares with their buy price so I can tell you what to sell. <button class="btn sm primary" id="padd">+ Add my shares</button></div>` :
      sells.map(h => holdRow(h, true)).join("") || `<div class="muted">Nothing to sell.</div>`}</div>
  <div class="card" style="margin-top:16px"><h2>2 · Buy</h2>
    ${!m.risk_on ? `<div class="muted">No new buys while the market is weak. In past weak markets, staying in cash avoided most big losses.</div>` :
      `${ps ? `<div class="note" style="margin:-4px 0 10px"><b>How reliable?</b> In 10 years of testing, picks like these beat the market over the next 20 days <b>${nf(ps.beat_market * 100, 0)}%</b> of the time and made money after costs <b>${nf(ps.profitable * 100, 0)}%</b> of the time (average ${pct(ps.avg * 100, 1)} vs ${pct(ps.market_avg * 100, 1)} for the market). Small edge — spread your money and always respect the sell price.</div>` : ""}
      <p class="muted" style="margin-top:-6px">${slots ? `About ${rs(perPick)} per stock from your cash of ${rs(cash)} (${slots} free slot${slots > 1 ? "s" : ""} of ${maxPos}).` : `<b>No new buys:</b> you already hold ${keeps.length} stocks and the plan is ${maxPos}. When the model says to sell one, its money goes into the top pick here.`} Change this in Portfolio → Settings.</p>
      ${s.buys.map(buyRow).join("") || `<div class="muted">No picks today.</div>`}`}</div>
  ${keeps.length ? `<div class="card" style="margin-top:16px"><h2>3 · Keep</h2>${keeps.map(h => holdRow(h, false)).join("")}</div>` : ""}
  <p class="muted" style="margin-top:18px;font-size:12.5px">These are probabilities, not guarantees. In the 10-year test more than half of all trades lost money and the strategy fell ${bt.strategy ? pct(bt.strategy.max_drawdown * 100, 0) : "a lot"} at its worst — see Model &amp; results. Never invest money you can’t afford to lose. <a href="#/today">Full market view →</a></p>`;
  if ($("#padd")) $("#padd").onclick = () => txForm();
}

// ---------------------------------------------------------------- all stocks
async function stocks() {
  const rows = await api("/api/stocks");
  const sectors = [...new Set(rows.map(r => r.sector))].sort();
  app.innerHTML = `<h1>All stocks</h1><p class="sub">${rows.length} companies · ranked by the model. Click a column to sort, a symbol for details, ☆ to watch.</p>
  <div class="toolbar">
    <input id="q" placeholder="Search symbol or name…" style="min-width:220px">
    <select id="fa"><option value="">All signals</option>${["BUY", "WATCH", "HOLD", "SELL", "AVOID"].map(a => `<option>${a}</option>`).join("")}</select>
    <select id="fs"><option value="">All sectors</option>${sectors.map(s => `<option>${esc(s)}</option>`).join("")}</select>
    <label class="muted"><input type="checkbox" id="fw"> Watchlist only</label>
    <label class="muted"><input type="checkbox" id="fl" checked> Liquid only</label>
  </div>
  <div class="card flat"><div class="tablewrap"><table id="t"><thead><tr>
    <th class="l" data-k="symbol">Symbol</th><th data-k="rank">Rank</th><th data-k="action" class="l">Signal</th><th data-k="score" data-desc=1>Score</th>
    <th data-k="price">Price</th><th data-k="chg1" data-desc=1>1D</th><th data-k="chg5" data-desc=1>1W</th><th data-k="chg20" data-desc=1>1M</th><th data-k="chg60" data-desc=1>3M</th><th data-k="chg1y" data-desc=1>1Y</th>
    <th data-k="rsi">RSI</th><th data-k="px_sma200" data-desc=1>vs 200d</th><th data-k="dist_hi52">from 52w high</th><th data-k="turnover20" data-desc=1>Avg turnover</th><th class="l" data-k="sector">Sector</th><th>☆</th>
  </tr></thead><tbody></tbody></table></div></div>`;
  const tb = $("#t tbody");
  const render = () => {
    const q = $("#q").value.trim().toUpperCase(), fa = $("#fa").value, fs = $("#fs").value, fw = $("#fw").checked, fl = $("#fl").checked;
    const f = rows.filter(r => (!q || r.symbol.includes(q) || (r.name || "").toUpperCase().includes(q)) && (!fa || r.action === fa) && (!fs || r.sector === fs) && (!fw || r.watch) && (!fl || r.tradable));
    tb.innerHTML = f.map(r => `<tr>
      <td>${symlink(r.symbol)}<div class="muted" style="font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis">${esc(r.name)}</div></td>
      <td>${r.rank ?? "—"}</td><td class="l">${badge(r.action)}</td><td>${nf(r.score, 0)}</td>
      <td>${nf(r.live ?? r.price)}${r.live ? ' <span class="dot live"></span>' : ""}</td><td>${pct(r.live_chg ?? r.chg1)}</td><td>${pct(r.chg5, 1)}</td><td>${pct(r.chg20, 1)}</td><td>${pct(r.chg60, 1)}</td><td>${pct(r.chg1y, 0)}</td>
      <td>${nf(r.rsi, 0)}</td><td>${pct(r.px_sma200, 1)}</td><td>${pct(r.dist_hi52, 1)}</td><td>${r.turnover20 ? nf(r.turnover20 / 1e5, 1) + " L" : "—"}</td>
      <td class="l muted">${esc(r.sector)}</td><td><span class="star ${r.watch ? "on" : ""}" data-s="${r.symbol}">${r.watch ? "★" : "☆"}</span></td></tr>`).join("") || `<tr><td colspan=16 class="empty">No matches</td></tr>`;
  };
  ["q", "fa", "fs", "fw", "fl"].forEach(id => $("#" + id).addEventListener("input", render));
  tb.addEventListener("click", async e => {
    const s = e.target.closest(".star"); if (!s) return;
    const r = rows.find(x => x.symbol === s.dataset.s); r.watch = !r.watch;
    await post("/api/watchlist", { symbol: r.symbol, remove: !r.watch }); render();
  });
  sortable($("#t"), rows, render); render();
}

// ---------------------------------------------------------------- stock detail
async function stock(sym) {
  const d = await api("/api/stock/" + encodeURIComponent(sym));
  const x = d.signal || {};
  const last = d.close[d.close.length - 1];
  app.innerHTML = `
  <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap">
    <div><h1>${esc(d.symbol)} ${x.action ? badge(x.action) : ""}</h1><p class="sub">${esc(d.name)} · ${esc(d.sector)}${x.rank ? ` · Model rank #${x.rank}` : ""}</p></div>
    <div style="display:flex;gap:8px">
      <button class="btn" id="bw">${d.watch ? "★ Watching" : "☆ Watch"}</button>
      <button class="btn primary" id="bb">+ Record buy</button><button class="btn" id="bs">Record sell</button><button class="btn" id="bc">Cost calculator</button>
    </div>
  </div>
  <div class="grid g4" style="margin-bottom:16px">
    <div class="card kpi"><div class="l">Last price (${x.date || ""})</div><div class="v">Rs ${nf(x.price ?? last)}</div><div class="l">${pct(x.chg1)} today · 1M ${pct(x.chg20, 1)} · 1Y ${pct(x.chg1y, 0)}</div></div>
    <div class="card kpi"><div class="l">Model score</div><div class="v">${nf(x.score, 0)}<span class="muted" style="font-size:14px">/100</span></div><div class="l">${esc(x.action_reason || "")}</div></div>
    <div class="card kpi"><div class="l">Trade plan</div><div style="margin-top:4px">Buy ≤ <b>${nf(x.entry_max)}</b> · Stop <b class="down">${nf(x.stop)}</b> · Target <b class="up">${nf(x.target)}</b></div><div class="l">Stop = 2.5× daily range below price</div></div>
    <div class="card kpi"><div class="l">Liquidity</div><div class="v">${x.turnover20 ? "Rs " + nf(x.turnover20 / 1e5, 1) + " L" : "—"}</div><div class="l">average daily turnover (20 days)</div></div>
  </div>
  <div class="grid g21">
    <div class="card">
      <div class="tabs" id="rng">${["3M", "6M", "1Y", "3Y"].map((r, i) => `<button class="${i == 2 ? "on" : ""}" data-r="${r}">${r}</button>`).join("")}</div>
      <div id="pc" class="chart" style="height:380px"></div>
      <h3 style="margin-top:10px">RSI (14)</h3><div id="rc" class="chart sm"></div>
      <h3 style="margin-top:10px">Model score history</h3><div id="sc" class="chart sm"></div>
      <p class="muted" style="font-size:12px">Prices adjusted for bonus/right shares so the chart has no false crashes. Lines: <span style="color:#d97706">20-day</span>, <span style="color:#2563eb">50-day</span>, <span style="color:#9333ea">200-day</span> averages.</p>
    </div>
    <div class="grid" style="align-content:start">
      <div class="card"><h2>Why this signal</h2><ul class="reasons">${(x.good || []).map(g => `<li class="g">${esc(g)}</li>`).join("")}${(x.bad || []).map(b => `<li class="b">${esc(b)}</li>`).join("")}</ul></div>
      <div class="card"><h2>Indicators</h2><table><tbody>
        <tr><td>RSI 14</td><td>${nf(x.rsi, 1)}</td></tr><tr><td>vs 50-day avg</td><td>${pct(x.px_sma50)}</td></tr><tr><td>vs 200-day avg</td><td>${pct(x.px_sma200)}</td></tr>
        <tr><td>From 52-week high</td><td>${pct(x.dist_hi52)}</td></tr><tr><td>Volume vs normal (5d)</td><td>${nf(x.vol_ratio)}×</td></tr>
        <tr><td>Chance to beat market (20d)</td><td>${nf(x.prob_top20, 0)}%</td></tr><tr><td>Expected vs market (20d)</td><td>${pct(x.exp_excess20)}</td></tr>
      </tbody></table></div>
      <div class="card"><h2>Bonus / right adjustments</h2>${d.events.length ? `<table><tbody>${d.events.slice().reverse().map(e => `<tr><td>${e.date}</td><td>price × ${e.factor}</td><td class="muted">≈ ${nf((1 / e.factor - 1) * 100, 1)}% bonus/right</td></tr>`).join("")}</tbody></table>` : `<div class="muted">None detected</div>`}</div>
      <div class="card"><h2>My trades in ${esc(d.symbol)}</h2>${d.transactions.length ? `<table><tbody>${d.transactions.map(t => `<tr><td>${t.date}</td><td>${badge(t.side === "BUY" ? "BUY" : "SELL")}</td><td>${nf(t.qty, 0)}</td><td>@ ${nf(t.price)}</td></tr>`).join("")}</tbody></table>` : `<div class="muted">None</div>`}</div>
    </div>
  </div>`;
  const pc = mkChart($("#pc"));
  const cs = pc.addCandlestickSeries({ upColor: "#059669", downColor: "#dc2626", borderVisible: false, wickUpColor: "#059669", wickDownColor: "#dc2626" });
  cs.setData(d.dates.map((t, i) => ({ time: t, open: d.open[i], high: d.high[i], low: d.low[i], close: d.close[i] })).filter(b => b.close));
  [["sma20", "#d97706"], ["sma50", "#2563eb"], ["sma200", "#9333ea"]].forEach(([k, col]) => pc.addLineSeries({ color: col, lineWidth: 1, priceLineVisible: false, lastValueVisible: false }).setData(series(d.dates, d[k])));
  const vs = pc.addHistogramSeries({ priceScaleId: "v", priceFormat: { type: "volume" } });
  pc.priceScale("v").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  vs.setData(d.dates.map((t, i) => ({ time: t, value: d.volume[i] || 0, color: d.close[i] >= d.open[i] ? "rgba(5,150,105,.35)" : "rgba(220,38,38,.35)" })));
  if (x.stop) cs.createPriceLine({ price: x.stop, color: "#dc2626", lineStyle: 2, title: "stop" });
  if (x.target) cs.createPriceLine({ price: x.target, color: "#059669", lineStyle: 2, title: "target" });
  (d.transactions || []).length && cs.setMarkers(d.transactions.filter(t => d.dates.includes(t.date)).map(t => ({ time: t.date, position: t.side === "BUY" ? "belowBar" : "aboveBar", color: t.side === "BUY" ? "#059669" : "#dc2626", shape: t.side === "BUY" ? "arrowUp" : "arrowDown", text: t.side })));
  const rc = mkChart($("#rc"));
  const rl = rc.addLineSeries({ color: "#d97706", lineWidth: 1 }); rl.setData(series(d.dates, d.rsi));
  rl.createPriceLine({ price: 70, color: "#dc2626", lineStyle: 2 }); rl.createPriceLine({ price: 30, color: "#059669", lineStyle: 2 });
  const scc = mkChart($("#sc"));
  const sl = scc.addAreaSeries({ lineColor: "#2563eb", topColor: "rgba(37,99,235,.3)", bottomColor: "rgba(37,99,235,0)", lineWidth: 1 });
  sl.setData(series(d.dates, d.score));
  const setRange = r => {
    const n = { "3M": 63, "6M": 126, "1Y": 250, "3Y": 9999 }[r];
    const from = d.dates[Math.max(0, d.dates.length - n)], to = d.dates[d.dates.length - 1];
    [pc, rc, scc].forEach(c => { try { c.timeScale().setVisibleRange({ from, to }); } catch (e) { c.timeScale().fitContent(); } });
  };
  setRange("1Y");
  $("#rng").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; $("#rng").querySelectorAll("button").forEach(z => z.classList.toggle("on", z === b)); setRange(b.dataset.r); });
  $("#bw").onclick = async () => { await post("/api/watchlist", { symbol: d.symbol, remove: d.watch }); route(); };
  $("#bb").onclick = () => txForm({ symbol: d.symbol, side: "BUY", price: x.price });
  $("#bs").onclick = () => txForm({ symbol: d.symbol, side: "SELL", price: x.price });
  $("#bc").onclick = () => calcForm(x.price, x.target);
}

// ---------------------------------------------------------------- forms
function txForm(pre = {}) {
  if (pre.kind === "unknown") pre = { ...pre, kind: "secondary", price: "", notes: "" };  // entering the real buy price
  modal(`<h2>${pre.id ? "Edit" : "Record"} a ${pre.side === "SELL" ? "sell" : "buy"}</h2>
  <div class="form" style="grid-template-columns:1fr 1fr">
    <label>Symbol<input id="fsym" value="${esc(pre.symbol || "")}" style="text-transform:uppercase"></label>
    <label>Buy / Sell<select id="fside"><option ${pre.side !== "SELL" ? "selected" : ""}>BUY</option><option ${pre.side === "SELL" ? "selected" : ""}>SELL</option></select></label>
    <label>Quantity (kitta)<input id="fqty" type="number" min="1" step="1" value="${pre.qty ?? ""}"></label>
    <label>Price per share<input id="fprice" type="number" step="0.1" value="${pre.price ?? ""}"></label>
    <label>Date<input id="fdate" type="date" value="${pre.date || today()}"></label>
    <label>Type<select id="fkind"><option value="secondary">Secondary market</option><option value="wacc">MeroShare WACC (fees included)</option><option value="ipo">IPO</option><option value="right">Right share</option><option value="bonus">Bonus share (price 0)</option></select></label>
  </div>
  <label class="muted" style="display:block;margin-top:10px">Note<input id="fnote" style="width:100%" placeholder="Why did I take this trade?" value="${esc(pre.notes || "")}"></label>
  <p class="muted" style="font-size:12px">Broker commission, SEBON fee and DP charge are added automatically for secondary-market trades. For a MeroShare WACC rate (My Purchase Source / bill base price), pick “MeroShare WACC” so fees aren’t added twice.</p>
  <div style="display:flex;gap:8px;justify-content:flex-end"><button class="btn" onclick="closeModal()">Cancel</button><button class="btn primary" id="fsave">Save</button></div>`);
  if (pre.kind) $("#fkind").value = pre.kind;
  $("#fkind").onchange = () => { if ($("#fkind").value === "bonus") $("#fprice").value = 0; if ($("#fkind").value === "ipo" || $("#fkind").value === "right") $("#fprice").value = 100; };
  $("#fsave").onclick = async () => {
    const body = { symbol: $("#fsym").value.trim(), side: $("#fside").value, qty: +$("#fqty").value, price: +$("#fprice").value, date: $("#fdate").value, kind: $("#fkind").value, notes: $("#fnote").value };
    if (!body.symbol || !(body.qty > 0) || $("#fprice").value.trim() === "" || !(body.price >= 0)) { toast("Fill symbol, quantity and price"); return; }
    if (body.kind === "secondary" && !(body.price > 0)) { toast("Enter the price you paid per share (only bonus shares can be 0)"); $("#fprice").focus(); return; }
    if (pre.id) await api("/api/transactions/" + pre.id, { method: "PUT", body: JSON.stringify(body) });
    else await post("/api/transactions", body);
    closeModal(); toast("Saved"); route();
  };
}
function calcForm(price, target) {
  modal(`<h2>Cost &amp; profit calculator</h2>
  <div class="form" style="grid-template-columns:1fr 1fr">
    <label>Quantity<input id="cq" type="number" value="100"></label><label>Buy price<input id="cp" type="number" step="0.1" value="${price ?? ""}"></label>
    <label>Sell price<input id="cs" type="number" step="0.1" value="${target ?? price ?? ""}"></label><label>Days held<input id="cd" type="number" value="30"></label>
  </div><div id="cr" style="margin-top:14px"></div>
  <div style="text-align:right;margin-top:10px"><button class="btn" onclick="closeModal()">Close</button></div>`);
  const go = async () => {
    const r = await post("/api/calc", { qty: +$("#cq").value, price: +$("#cp").value, sell: +$("#cs").value, days: +$("#cd").value });
    $("#cr").innerHTML = `<table><tbody>
      <tr><td>You pay (incl. fees)</td><td>${rs(r.buy_total, 2)}</td></tr><tr><td>Buy fees</td><td>${rs(r.buy_fees, 2)}</td></tr>
      <tr><td>Sell fees</td><td>${rs(r.sell_fees, 2)}</td></tr><tr><td>Capital gains tax</td><td>${rs(r.tax, 2)}</td></tr>
      <tr><td>You receive</td><td>${rs(r.net_receive, 2)}</td></tr><tr><td><b>Net profit</b></td><td><b>${sgn(r.profit, 2)}</b> (${pct(r.profit_pct)})</td></tr>
      <tr><td>Break-even sell price</td><td><b>${nf(r.breakeven)}</b></td></tr></tbody></table>`;
  };
  ["cq", "cp", "cs", "cd"].forEach(id => $("#" + id).addEventListener("input", go)); go();
}
window.closeModal = closeModal;

// ---------------------------------------------------------------- profit
const monthName = m => new Date(m + "-15").toLocaleDateString("en-GB", { month: "long", year: "numeric" });
function targetForm(cur) {
  modal(`<h2>Monthly profit target</h2>
    <label class="muted" style="display:flex;flex-direction:column;gap:4px">Target per month (Rs)<input id="tg" type="number" min="0" step="1000" value="${cur}"></label>
    <div style="text-align:right;margin-top:14px"><button class="btn" onclick="closeModal()">Cancel</button> <button class="btn primary" id="tgs">Save</button></div>`);
  $("#tg").focus();
  $("#tgs").onclick = async () => { await post("/api/settings", { monthly_target: +$("#tg").value || 0 }); closeModal(); toast("Target saved"); route(); };
}
async function profit() {
  const d = await api("/api/profit");
  const t = d.totals, c = d.current, R = d.realistic;
  const done = d.target ? Math.max(0, Math.min(100, c.profit / d.target * 100)) : 0;
  const full = d.months.filter(m => !m.partial && !m.sells_only && m.month !== d.month);
  const monthsHit = full.filter(m => m.hit).length, monthsFull = full.length;
  app.innerHTML = `
  <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;flex-wrap:wrap">
    <div><h1>Profit</h1><p class="sub">All figures are after broker commission, SEBON fee, DP charge and capital gains tax.</p></div>
    <button class="btn" id="tgb">Change target</button>
  </div>
  <div class="card hero" style="margin-bottom:16px">
    <div class="muted" style="font-weight:600">${monthName(d.month)}</div>
    <div class="big" style="margin-top:6px">${sgn(c.profit)} <span class="of">of ${rs(d.target)} target</span></div>
    <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(done)}" aria-label="Monthly target progress"><i style="width:${done}%"></i></div>
    <div class="facts">
      ${c.profit >= d.target ? `<span><b class="up">Target reached</b></span>` : `<span><b>${rs(d.remaining)}</b> to go</span>
      <span><b>${d.trading_days_left}</b> trading day${d.trading_days_left === 1 ? "" : "s"} left</span>
      ${d.per_day_needed ? `<span>about <b>${rs(d.per_day_needed)}</b> a day needed</span>` : ""}`}
      <span>From sells <b>${sgn(c.booked)}</b></span><span>On paper <b>${sgn(c.paper)}</b></span>
    </div>
    ${c.partial ? `<p class="muted" style="font-size:12.5px;margin:12px 0 0">Tracking started ${d.tracking_since}, so this month only counts the change since then.</p>` : ""}
  </div>
  <div class="grid g4" style="margin-bottom:16px">
    <div class="card stat"><div class="l">Total profit, all time</div><div class="v">${sgn(t.total_pnl)}</div><div class="l">${pct(t.total_pnl_pct)} on money put in</div></div>
    <div class="card stat"><div class="l">Money put in (all buys)</div><div class="v">${rs(t.invested_ever)}</div><div class="l">including fees</div></div>
    <div class="card stat"><div class="l">Shares are worth now</div><div class="v">${rs(t.value)}</div><div class="l">Today ${sgn(t.day_change)}</div></div>
    <div class="card stat"><div class="l">Months target reached</div><div class="v">${monthsFull ? `${monthsHit} of ${monthsFull}` : "—"}</div><div class="l">${monthsFull ? "full months only" : "first full month: next month"}</div></div>
  </div>
  <div class="note" style="margin-bottom:16px"><b>Reality check.</b> Over the 10-year test the model made about <b>${nf(R.yearly_pct, 0)}% a year</b>. On your ${rs(t.value)} that is roughly <b>${rs(R.monthly_on_value)} a month</b> on average, and some months will be negative.
    Making ${rs(d.target)} every month at that rate needs about <b>${rs(R.capital_needed)}</b> invested. With your current money it would need about ${nf(d.target / Math.max(t.value, 1) * 100, 0)}% profit every month, which only very risky trading can produce, and that kind of trading usually loses money.
    Treat the target as a goal to track, not a reason to take bigger risks.</div>
  <div class="card" style="margin-bottom:16px"><h2>Fastest safe way to Rs ${nf(d.target, 0)} a month: add money every month</h2>
    <p class="muted" style="margin-top:-4px">Profit grows with the money invested. Trading harder doesn’t make more: in testing, holding just 1 stock or trading every week lost money after fees. Adding money each month does.</p>
    <div class="form" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr));max-width:620px">
      <label for="dep">I can add every month (Rs)<input id="dep" type="number" min="0" step="1000" value="10000" inputmode="numeric"></label>
      <label for="yr">Yearly return to assume (%)<input id="yr" type="number" min="1" max="40" step="1" value="${nf(R.yearly_pct, 0)}"></label>
    </div>
    <div id="path" style="margin-top:14px" aria-live="polite"></div></div>
  <div class="grid g2">
    <div class="card"><h2>Month by month</h2>${d.months.length ? `<table><thead><tr><th class="l">Month</th><th>Profit</th><th>From sells</th><th>On paper</th><th class="l">Target</th></tr></thead><tbody>
      ${d.months.map(m => `<tr><td>${monthName(m.month)}${m.partial ? ' <span class="muted">(part)</span>' : ""}</td><td><b>${sgn(m.profit)}</b></td><td>${sgn(m.booked)}</td><td>${m.sells_only ? '<span class="muted">not tracked</span>' : sgn(m.paper)}</td>
        <td class="l">${m.sells_only ? '<span class="muted">—</span>' : m.hit ? '<span class="pill ok">Reached</span>' : m.month === d.month ? '<span class="pill no">In progress</span>' : '<span class="pill no">Missed</span>'}</td></tr>`).join("")}</tbody></table>` : `<div class="muted">Starts after the first daily update.</div>`}</div>
    <div class="card"><h2>Total profit over time</h2>${(t && d.months.length) ? `<div id="pp" class="chart" style="height:240px"></div>` : `<div class="muted">Builds up automatically, one point per trading day.</div>`}</div>
  </div>
  <div class="card" style="margin-top:16px"><h2>Sold shares</h2>${d.realized.length ? `<table><thead><tr><th class="l">Date</th><th class="l">Stock</th><th>Kitta</th><th>Sold at</th><th>Profit</th><th>%</th></tr></thead><tbody>
    ${d.realized.map(r => `<tr><td>${r.date}</td><td class="l">${symlink(r.symbol)}</td><td>${nf(r.qty, 0)}</td><td>${nf(r.price)}</td><td>${sgn(r.pnl)}</td><td>${pct(r.pct)}</td></tr>`).join("")}</tbody></table>` : `<div class="muted">Nothing sold yet.</div>`}</div>`;
  $("#tgb").onclick = () => targetForm(d.target);
  const drawPath = () => {  // month-by-month compounding until profit at that size ≈ target
    const dep = Math.max(0, +$("#dep").value || 0), y = Math.max(0.01, (+$("#yr").value || 1) / 100), r = (1 + y) ** (1 / 12) - 1;
    const need = d.target * 12 / y;
    let v = t.value, m = 0;
    while (v < need && m < 600) { v = v * (1 + r) + dep; m++; }
    const when = new Date(); when.setMonth(when.getMonth() + m);
    $("#path").innerHTML = m >= 600 ? `<div class="muted">Not reachable with these numbers. Try adding more each month.</div>` :
      `<div class="facts" style="font-size:14px"><span>Money needed <b>${rs(need)}</b></span><span>You have <b>${rs(t.value)}</b></span>
       <span>Reached in about <b>${m < 12 ? m + " months" : nf(m / 12, 1) + " years"}</b> (${when.toLocaleDateString("en-GB", { month: "short", year: "numeric" })})</span>
       <span>Of that, you add <b>${rs(dep * m)}</b> yourself</span></div>
       <div class="muted" style="font-size:12.5px;margin-top:6px">Assumes ${nf(y * 100, 0)}% a year every year. Real years go up and down, some lose money.</div>`;
  };
  ["dep", "yr"].forEach(id => $("#" + id).addEventListener("input", drawPath)); drawPath();
  const pv = await api("/api/portfolio");
  if ($("#pp") && pv.snapshots.length) {
    const ch = mkChart($("#pp"));
    ch.addBaselineSeries({ baseValue: { type: "price", price: 0 }, topLineColor: "#047857", topFillColor1: "rgba(4,120,87,.18)", topFillColor2: "rgba(4,120,87,.02)",
      bottomLineColor: "#dc2626", bottomFillColor1: "rgba(220,38,38,.02)", bottomFillColor2: "rgba(220,38,38,.18)", lineWidth: 2 })
      .setData(pv.snapshots.map(x => ({ time: x.date, value: (x.pnl ?? (x.value - x.cost + x.realized)) - x.realized
        + d.realized.filter(r => r.date <= x.date).reduce((a, r) => a + r.pnl, 0) })));
    ch.timeScale().fitContent();
  }
}

// ---------------------------------------------------------------- portfolio
const KIND = { secondary: "Market", wacc: "MeroShare WACC", ipo: "IPO", right: "Right", bonus: "Bonus", unknown: "Price missing" };
async function portfolio() {
  const pf = await api("/api/portfolio");
  const t = pf.totals, s = pf.settings;
  app.innerHTML = `
  <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:10px">
    <div><h1>Portfolio</h1><p class="sub">The shares you hold now. Profit is what you would keep if you sold today, after all fees and tax. ${pf.market_open ? '<span class="dot live"></span>Live prices' : ""}</p></div>
    <div style="display:flex;gap:8px"><button class="btn primary" id="add">+ Add buy / sell</button><button class="btn" id="calc">Calculator</button><button class="btn" id="set">Settings</button></div>
  </div>
  <div class="grid g3" style="margin-bottom:16px">
    <div class="card stat"><div class="l">Worth now</div><div class="v">${rs(t.value)}</div><div class="l">Today ${sgn(t.day_change)}</div></div>
    <div class="card stat"><div class="l">You paid${t.unknown ? ` (${t.unknown} without price)` : ""}</div><div class="v">${t.unknown && !t.cost ? "—" : rs(t.cost)}</div><div class="l">including buy fees</div></div>
    <div class="card stat"><div class="l">Profit if you sell everything today</div><div class="v">${t.unknown && !t.cost ? "—" : sgn(t.pnl_net)}</div><div class="l"><a href="#/profit">Total and monthly profit →</a></div></div>
  </div>
  <div class="card flat" style="margin-bottom:16px"><div class="tablewrap"><table><thead><tr>
    <th class="l">Stock</th><th>Kitta</th><th>Paid / share</th><th>Price now</th><th>Worth</th><th>Profit</th><th class="l">Model says</th><th class="l">Notes</th>
  </tr></thead><tbody>${pf.holdings.map(h => `<tr>
    <td>${symlink(h.symbol)}<div class="muted" style="font-size:11.5px">${h.held_days != null ? `held ${h.held_days} days` : ""}</div></td><td>${nf(h.qty, 0)}</td>
    <td>${h.price_unknown ? `<button class="btn sm primary" data-fix="${esc(h.symbol)}">Enter price</button>` : nf(h.wacc)}</td>
    <td>${nf(h.ltp)}<div style="font-size:11.5px">${pct(h.day_chg_pct)}</div></td><td>${nf(h.value, 0)}</td>
    <td>${sgn(h.pnl_net)}<div style="font-size:11.5px">${pct(h.pnl_net_pct)}</div></td><td class="l">${badge(h.action)}</td>
    <td class="l" style="white-space:normal;min-width:200px">${h.alerts.map(a => `<div class="alert">${esc(a)}</div>`).join("") || (h.stop ? `<span class="muted">Sell if it falls to ${nf(h.stop)}</span>` : '<span class="muted">—</span>')}</td></tr>`).join("") || `<tr><td colspan=8 class="empty">No shares yet. Click “Add buy / sell” and enter your MeroShare holdings with their buy price.</td></tr>`}
  </tbody></table></div></div>
  ${t.unknown ? `<div class="note" style="margin:-4px 0 16px">Buy price not entered for ${t.unknown} stock${t.unknown > 1 ? "s" : ""}, so cost and profit are left blank instead of guessed. Click <b>Enter price</b> and type your rate from MeroShare → My Purchase Source.</div>` : ""}
  <div class="card" style="margin-bottom:16px"><h2>Value over time</h2>${pf.snapshots.length > 1 ? `<div id="pv" class="chart" style="height:240px"></div><p class="muted" style="font-size:12px;margin:6px 0 0"><span style="color:#059669">━</span> worth &nbsp; <span style="color:#64748b">┅</span> what you paid</p>` : `<div class="muted">Builds up automatically, one point per trading day.</div>`}</div>
  <details class="card box"><summary>All buys and sells (${pf.transactions.length})</summary><div class="tablewrap" style="max-height:400px;margin-top:12px"><table><thead><tr><th class="l">Date</th><th class="l">Stock</th><th class="l">Side</th><th class="l">Type</th><th>Kitta</th><th>Price</th><th class="l">Note</th><th></th></tr></thead><tbody>
    ${pf.transactions.map(x => `<tr><td>${x.date}</td><td class="l">${symlink(x.symbol)}</td><td class="l">${badge(x.side)}</td><td class="l muted">${esc(KIND[x.kind] || x.kind)}</td><td>${nf(x.qty, 0)}</td><td>${nf(x.price)}</td><td class="l muted">${esc(x.notes)}</td><td><button class="btn sm" data-edit="${x.id}">Edit</button> <button class="btn danger sm" data-del="${x.id}">Delete</button></td></tr>`).join("") || `<tr><td colspan=8 class="empty">No transactions</td></tr>`}
  </tbody></table></div></details>`;
  $("#add").onclick = () => txForm();
  $("#calc").onclick = () => calcForm();
  $("#set").onclick = () => {
    modal(`<h2>Settings</h2><div class="form" style="grid-template-columns:1fr 1fr">
      <label>Total capital (Rs)<input id="s1" type="number" value="${s.capital}"></label>
      <label>My stop-loss %<input id="s2" type="number" value="${s.stop_pct}"></label>
      <label>Max stocks to hold<input id="s3" type="number" value="${s.max_positions}"></label>
      <label>DP charge per trade (Rs)<input id="s4" type="number" value="${s.dp_fee}"></label>
      <label>Monthly profit target (Rs)<input id="s5" type="number" step="1000" value="${s.monthly_target}"></label></div>
      <p class="muted" style="font-size:12px">Suggested size per stock = capital ÷ max stocks. With Rs ${nf(+s.capital, 0)} and ${s.max_positions} stocks → about Rs ${nf(s.capital / s.max_positions, 0)} each.</p>
      <div style="text-align:right"><button class="btn" onclick="closeModal()">Cancel</button> <button class="btn primary" id="ss">Save</button></div>`);
    $("#ss").onclick = async () => { await post("/api/settings", { capital: $("#s1").value, stop_pct: $("#s2").value, max_positions: $("#s3").value, dp_fee: $("#s4").value, monthly_target: $("#s5").value }); closeModal(); route(); };
  };
  app.querySelectorAll("[data-fix]").forEach(b => b.onclick = () => txForm(pf.transactions.find(x => x.symbol === b.dataset.fix && x.kind === "unknown")));
  app.querySelectorAll("[data-edit]").forEach(b => b.onclick = () => txForm(pf.transactions.find(x => x.id === +b.dataset.edit)));
  app.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => {
    if (!confirm("Delete this transaction?")) return;
    await api("/api/transactions/" + b.dataset.del, { method: "DELETE" }); route();
  });
  if (pf.snapshots.length > 1) {
    const c = mkChart($("#pv"));
    c.addLineSeries({ color: "#059669", lineWidth: 2, title: "value" }).setData(pf.snapshots.map(x => ({ time: x.date, value: x.value })));
    c.addLineSeries({ color: "#64748b", lineWidth: 1, lineStyle: 2, title: "cost" }).setData(pf.snapshots.map(x => ({ time: x.date, value: x.cost })));
    c.timeScale().fitContent();
  }
}

// ---------------------------------------------------------------- daily record
async function records() {
  const r = await api("/api/records");
  app.innerHTML = `<h1>Daily record</h1><p class="sub">Every trading day the app saves the market mood and the buy picks. Below: how each day’s picks have done since (vs. latest price). Add your own notes.</p>
  ${r.records.length ? r.records.map(x => `<div class="card" style="margin-bottom:12px">
    <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px">
      <div><b style="font-size:16px">${x.date}</b> &nbsp; <span class="mood ${x.market.risk_on ? "on" : "off"}"></span>${esc(x.market.mood)} · breadth ${x.market.breadth50}% · <span class="up">${x.market.advances}↑</span> <span class="down">${x.market.declines}↓</span> · market ${pct(x.market.proxy_chg1)}</div>
      <div>${x.avg_ret !== null ? `Picks since then: <b>${pct(x.avg_ret)}</b>${x.market_ret != null ? ` vs market ${pct(x.market_ret)}` : ""}` : ""} ${x.portfolio ? ` · Portfolio ${rs(x.portfolio.value)}` : ""}</div>
    </div>
    ${x.picks.length ? `<table style="margin-top:8px"><thead><tr><th class="l">Pick</th><th>Price then</th><th>Now</th><th>Change</th><th>Stop</th><th>Target</th></tr></thead><tbody>${x.picks.map(p => `<tr><td>${symlink(p.symbol)}</td><td>${nf(p.price)}</td><td>${nf(p.now)}</td><td>${pct(p.ret)}</td><td>${nf(p.stop)}</td><td>${nf(p.target)}</td></tr>`).join("")}</tbody></table>` : `<div class="muted" style="margin-top:6px">No buy picks this day${x.market.risk_on ? "" : " (market weak — stayed in cash)"}.</div>`}
    <textarea data-d="${x.date}" placeholder="My notes for this day…" style="width:100%;margin-top:10px;min-height:38px">${esc(x.note)}</textarea>
  </div>`).join("") : `<div class="card empty">The first record is saved after the first update. One is added every trading day.</div>`}`;
  app.querySelectorAll("textarea[data-d]").forEach(t => t.addEventListener("change", async () => { await post("/api/journal", { date: t.dataset.d, note: t.value }); toast("Note saved"); }));
}

// ---------------------------------------------------------------- model
async function model() {
  const [b, st, mh, rec] = await Promise.all([api("/api/backtest"), api("/api/status"),
    api("/api/model_history").catch(() => []), api("/api/records").catch(() => ({}))]);
  const live = rec.live || {}, last = mh[mh.length - 1];
  if (!b.strategy) { app.innerHTML = `<div class="card empty">Backtest not run yet.</div>`; return; }
  const S = b.strategy, B = b.buy_and_hold, N = b.no_market_filter, T = b.trades;
  const maxDec = Math.max(...b.deciles_fwd20.map(Math.abs));
  const recent = b.yearly.filter(y => y.year >= 2021), yrs = recent.length - 0.2;  // 2026 is ~10 months
  const grow = k => recent.reduce((a, y) => a * (1 + y[k]), 1) ** (1 / yrs) - 1;
  const ps = b.pick_stats || {};
  app.innerHTML = `<h1>Model &amp; honest results</h1>
  <p class="sub">Walk-forward test ${b.period[0]} → ${b.period[1]}: each year the model is trained only on earlier years, then tested on a year it has never seen. Trades are bought and sold at the <b>next day’s opening price</b> (you only see a signal after the close) with ${nf((b.settings.slippage || 0) * 100, 1)}% slippage, and all results are <b>after</b> broker commission, SEBON fee and 7.5% capital gains tax.</p>
  <div class="note" style="margin-bottom:16px"><b>Honest verdict.</b> Over the full test the model earned less than simply holding every liquid share (${nf(S.cagr * 100, 1)}% vs ${nf(B.cagr * 100, 1)}% a year), mainly because it sat in cash during the 2016 and 2020 rallies. In the last ${recent.length} years, with 150+ stocks to choose from, it did ${grow("model") > grow("market") ? "slightly better" : "worse"} than holding (${nf(grow("model") * 100, 1)}% vs ${nf(grow("market") * 100, 1)}% a year) and fell less in bad years. Its picks beat the market only ${nf((ps.beat_market || 0) * 100, 0)}% of the time — a small edge, not a prediction you can rely on for any one stock.</div>
  <div class="card" style="margin-bottom:16px"><h2>Learning every day</h2>
    <p class="muted" style="margin-top:-4px">After each market close the model retrains on all data including the newest day. It also tries ${3} different settings on the last ~4 months it has not trained on, and only switches when another setting is clearly better. This stops it from chasing luck.</p>
    <div class="grid g3">
      <div class="stat"><div class="l">Real picks since ${live.since || "—"}</div><div class="v">${live.picks ? `${pct(live.avg)} <span class="muted" style="font-size:14px">vs market ${pct(live.market)}</span>` : "—"}</div><div class="l">${live.picks ? `${live.picks} picks over ${live.days} days · ${nf(live.beat, 0)}% beat the market so far` : "fills in as days pass"}</div></div>
      <div class="stat"><div class="l">Latest check on unseen months</div><div class="v">${last ? `${nf(last.top_beat, 0)}%` : "—"}</div><div class="l">${last ? `of top picks beat the market (setting “${esc(last.config)}”, ${last.date})` : "after the next update"}</div></div>
      <div class="stat"><div class="l">Learned from</div><div class="v">${last ? nf(last.rows, 0) : "—"}</div><div class="l">${last ? `stock-days, up to ${last.train_until}` : ""}</div></div>
    </div>
    ${live.picks && live.days < 20 ? `<p class="muted" style="font-size:12.5px;margin:10px 0 0">Only ${live.days} days of real picks so far, too few to judge. Give it a few months.</p>` : ""}
    ${mh.length > 1 ? `<details style="margin-top:10px"><summary class="muted" style="cursor:pointer">Daily check history</summary><table style="margin-top:8px"><thead><tr><th class="l">Date</th><th class="l">Setting</th><th>Top picks beat market</th><th>Avg extra return</th></tr></thead><tbody>
      ${mh.slice().reverse().slice(0, 60).map(h => `<tr><td>${h.date}</td><td class="l">${esc(h.config)}${h.switched ? ' <span class="pill ok">switched</span>' : ""}</td><td>${nf(h.top_beat, 0)}%</td><td>${pct(h.top_excess)}</td></tr>`).join("")}</tbody></table></details>` : ""}
  </div>
  <div class="grid g3" style="margin-bottom:16px">
    <div class="card kpi"><div class="l">Model strategy — yearly return</div><div class="v ${S.cagr >= B.cagr ? "up" : "down"}">${nf(S.cagr * 100, 1)}%</div><div class="l">worst fall ${pct(S.max_drawdown * 100, 0)} · total ${pct(S.total * 100, 0)}</div></div>
    <div class="card kpi"><div class="l">Buy &amp; hold all liquid stocks</div><div class="v">${nf(B.cagr * 100, 1)}%</div><div class="l">worst fall ${pct(B.max_drawdown * 100, 0)} · total ${pct(B.total * 100, 0)}</div></div>
    <div class="card kpi"><div class="l">Same picks, no market filter</div><div class="v">${nf(N.cagr * 100, 1)}%</div><div class="l">worst fall ${pct(N.max_drawdown * 100, 0)} — shows why the healthy/weak market filter matters</div></div>
  </div>
  <div class="card" style="margin-bottom:16px"><h2>Growth of Rs 1 (log scale)</h2><div id="eq" class="chart"></div>
    <p class="muted" style="font-size:12px"><span style="color:#059669">■</span> model strategy &nbsp; <span style="color:#64748b">■</span> buy &amp; hold &nbsp; <span style="color:#d97706">■</span> picks without market filter</p></div>
  <div class="grid g2">
    <div class="card"><h2>Year by year</h2><table><thead><tr><th class="l">Year</th><th>Model</th><th>Buy &amp; hold</th><th>Difference</th></tr></thead><tbody>
      ${b.yearly.map(y => `<tr><td>${y.year}</td><td>${pct(y.model * 100, 1)}</td><td>${pct(y.market * 100, 1)}</td><td>${pct((y.model - y.market) * 100, 1)}</td></tr>`).join("")}</tbody></table></div>
    <div class="card"><h2>Does a higher score really mean better returns?</h2>
      <p class="muted" style="font-size:12.5px">Average 20-day return of stocks grouped by model score (left = lowest 10%, right = highest 10%), on unseen years.</p>
      <div class="bars">${b.deciles_fwd20.map(v => `<div style="height:${Math.abs(v) / maxDec * 100}%;background:${v >= 0 ? "#2563eb" : "#dc2626"}"><span>${nf(v, 1)}%</span></div>`).join("")}</div>
      <div style="display:flex;justify-content:space-between" class="muted"><small>lowest score</small><small>highest score</small></div>
      <h2 style="margin-top:18px">Daily top-${b.settings.top_n} picks, 20 days later</h2>
      <table><tbody><tr><td>Beat the market</td><td>${nf((ps.beat_market || 0) * 100, 0)}%</td></tr>
      <tr><td>Made money after costs</td><td>${nf((ps.profitable || 0) * 100, 0)}%</td></tr>
      <tr><td>Average pick vs average stock</td><td>${pct((ps.avg || 0) * 100)} vs ${pct((ps.market_avg || 0) * 100)}</td></tr></tbody></table>
      <h2 style="margin-top:18px">Trades</h2>
      <table><tbody><tr><td>Trades per year</td><td>${T.per_year}</td></tr><tr><td>Winning trades</td><td>${nf(T.win_rate * 100, 0)}%</td></tr>
      <tr><td>Average trade (after costs)</td><td>${pct(T.avg * 100)}</td></tr><tr><td>Average win / average loss</td><td>${pct(T.avg_win * 100)} / ${pct(T.avg_loss * 100)}</td></tr></tbody></table>
    </div>
  </div>
  <div class="card" style="margin-top:16px"><h2>Most recent simulated trades</h2><table><thead><tr><th class="l">Symbol</th><th>Bought</th><th>Sold</th><th>Return (net)</th></tr></thead><tbody>
    ${T.recent.map(x => `<tr><td>${symlink(x.symbol)}</td><td>${x.buy}</td><td>${x.sell}</td><td>${pct(x.ret * 100)}</td></tr>`).join("")}</tbody></table></div>
  <div class="card prose" style="margin-top:16px">
    <h2>How it works</h2>
    <ol>
      <li><b>Data</b> — daily prices for ~370 NEPSE companies since 2011, adjusted for bonus and right shares. Updated after every market close from GitHub, NEPSE (nepalstock.com.np) and ShareSansar.</li>
      <li><b>37 measurements per stock per day</b> — trend (20/50/200-day averages), momentum (1 week to 6 months), RSI, MACD, Bollinger bands, volatility, volume surges, 52-week high/low, strength versus the market, and market-wide health (breadth).</li>
      <li><b>3 machine-learning models</b> (gradient-boosted trees) predict which stocks will <i>beat the market</i> over the next 10–20 trading days. Their rankings are averaged into a 0–100 score.</li>
      <li><b>Rules</b> — buy the top ${b.settings.top_n} only when the market is healthy (>${b.settings.breadth_risk_on * 100}% of stocks above their 50-day average); re-check every ~2 weeks; sell when a stock drops out of the top ${b.settings.exit_rank} or the market turns weak.</li>
      <li><b>Retrained after every market close</b> on the newest data, with a daily check of its settings on unseen months. Last trained: ${st.model ? st.model.trained.slice(0, 16).replace("T", " ") + ` on ${nf(st.model.rows, 0)} examples` : "—"}.</li>
    </ol>
    <h2>Limits — please read</h2>
    <ul>
      <li>More than half of the trades lose money. The profit comes from winners being bigger than losers, and from avoiding bad markets. Follow the rules consistently — cherry-picking breaks it.</li>
      <li>The edge over simply holding the market is modest. The biggest benefit is the smaller losses in crashes.</li>
      <li>The model only sees prices and volume. It doesn’t know about news, NRB policy, AGMs, dividend announcements or operator manipulation. Check the news before you buy.</li>
      <li>Past results don’t guarantee future returns. Never invest borrowed money or money you can’t afford to lose.</li>
    </ul>
  </div>`;
  const c = mkChart($("#eq"), { rightPriceScale: { mode: 1, borderColor: "#d6dde5" } });
  c.addLineSeries({ color: "#64748b", lineWidth: 1 }).setData(series(b.curve.dates, b.curve.market));
  c.addLineSeries({ color: "#d97706", lineWidth: 1 }).setData(series(b.curve.dates, b.curve.no_filter));
  c.addLineSeries({ color: "#059669", lineWidth: 2 }).setData(series(b.curve.dates, b.curve.model));
  c.timeScale().fitContent();
}

// ---------------------------------------------------------------- guide
async function guide() {
  app.innerHTML = `<div class="card prose"><h1>How to use NEPSE Desk</h1>
  <h2>Every trading day (5 minutes, after 3:30 PM)</h2>
  <ol>
    <li>Keep the app running. It updates itself after market close (Sunday–Thursday). Or click <b>Update now</b>.</li>
    <li>Open <b>Plan</b>. It says whether the market is healthy (OK to buy) or weak (don’t buy anything new).</li>
    <li>Do <b>1 · Sell</b> first: sell anything listed there.</li>
    <li>If the market is healthy, buy from <b>2 · Buy</b> next morning, only at or below the price shown. Don’t chase a stock that opens much higher.</li>
    <li>Record every buy and sell in <b>Portfolio</b> so alerts and profit stay correct. Check <b>Profit</b> for your monthly target.</li>
    <li>Write a one-line note in <b>Daily record</b> — why you did (or didn’t) trade. Reviewing this is how you improve.</li>
  </ol>
  <h2>Position size</h2>
  <p>Split your capital equally across 3 stocks (set this in Portfolio → Settings). In testing, 3 stocks did better than 5, and holding only 1 or 2 lost a lot. NEPSE costs roughly 1% for a full buy+sell plus 7.5% tax on profit, so trading too often eats returns. The model re-checks every ~2 weeks for this reason.</p>
  <h2>What the signals mean</h2>
  <ul>
    <li>${badge("BUY")} top 3 model picks, and the market is healthy.</li>
    <li>${badge("WATCH")} ranked 4–15: almost a buy. Watch it.</li>
    <li>${badge("HOLD")} ranked 16–30: keep it if you own it, don’t buy new.</li>
    <li>${badge("SELL")} ranked below 30, or the market is weak. Sell if you own it.</li>
    <li>${badge("AVOID")} too little trading volume — hard to buy or sell at a fair price.</li>
  </ul>
  <h2>Stop-loss and target</h2>
  <p>The stop is set 2.5× the stock’s normal daily range below the price, and the target 3× above. They are guides, not orders. In testing, the ranking-based sell rule worked better than tight stops. Your own stop % in Settings gives you a hard safety alert.</p>
  </div>`;
}

refreshStatus().then(route);
