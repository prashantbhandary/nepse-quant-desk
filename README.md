# NEPSE Desk

**A private, local trading assistant for the Nepal Stock Exchange (NEPSE).**

NEPSE Desk runs on your own computer. Every trading day it downloads fresh prices for ~370 listed companies, ranks every stock with a machine-learning model, and tells you what to buy, what to sell and whether the market is safe to enter. It also tracks your portfolio with real profit after fees and tax. Your portfolio is stored only on your machine and never leaves it.

![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![License: MIT](https://img.shields.io/badge/license-MIT-green) ![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)

> ⚠️ **Not financial advice.** The model has a small, real ranking edge, but in an honest backtest it did **not** beat simple buy-and-hold over 2016–2026. Read the [research write-up](docs/RESEARCH.md) before trusting it with money.

---

## Contents

- [Features](#features)
- [Quick start](#quick-start)
- [Pages](#pages)
- [How it works](#how-it-works)
- [Backtest results](#backtest-results)
- [Data sources](#data-sources)
- [Project structure](#project-structure)
- [Command-line usage](#command-line-usage)
- [Privacy](#privacy)
- [Research write-up](#research-write-up)
- [License](#license)
- [Author](#author)

---

## Features

- **Daily signals:** ranks every liquid NEPSE stock and gives a next-day buy/sell plan for a top-3 portfolio.
- **Market health check:** a breadth filter says when to stay in cash.
- **Portfolio tracker:** cost basis, live value and profit after broker commission, SEBON fee, DP charge and capital-gains tax.
- **Monthly profit target:** tracks this month's profit against a goal you set.
- **Stock explorer:** signal, score, 1D/1W/1M/3M/1Y change, RSI and trend for every company, with an interactive chart and a cost calculator.
- **Daily record:** logs every recommendation and how it actually turned out, with your own notes.
- **Self-updating model:** retrains after every market close and refreshes live prices every 3 minutes while the market is open.
- **Honest backtest:** a walk-forward test with next-open execution, slippage, fees and tax.

---

## Quick start

**Requirements:** Python 3.11+ (3.9+ works, but without live prices, the NEPSE index or sectors) and an internet connection.

```bash
git clone https://github.com/prashantbhandary/nepse-quant-desk.git
cd nepse-quant-desk
chmod +x start.sh install_autostart.sh
./start.sh
```

Your browser opens **http://localhost:8050**.

| | Time |
|---|---|
| First run (install packages, download 15 years of data, train the model, run the backtest) | ~5 min |
| Later runs | a few seconds |

Leave it running. After the market closes (3:20 PM, Sunday–Thursday) it updates itself.

**Optional, macOS: start at login.**

```bash
./install_autostart.sh          # run in the background whenever you log in
./install_autostart.sh remove   # undo
```

To use another port: `PORT=8051 ./start.sh`.

---

## Pages

| Page | What it shows |
|---|---|
| **Plan** | What to sell and buy on the next trading day, market health, and this month's profit vs target |
| **Profit** | Monthly profit vs target, total profit, month-by-month results, closed trades and a reality check on the target |
| **Portfolio** | Holdings, cost, value and profit after all charges; full buy/sell history |
| **Stocks** | Every company with signal, score, returns, RSI and trend. Click one for its chart, the reasons for its signal and a cost calculator |
| **More** | Market today, daily record, model and backtest results, how-to guide |

> **Tip:** when entering a rate from MeroShare → *My Purchase Source*, or the base price on a sell bill, choose the type **MeroShare WACC**. That rate already includes buy fees, so the app does not add them again.

Fee rates live in [`engine/model.py`](engine/model.py). Check them against your broker's current rates.

---

## How it works

```
 Data sources ──► engine/sources.py ──► data/prices/*.csv
                                              │
                  engine/features.py ◄────────┘  bonus/right adjustment, 35+ indicators
                          │
                  engine/pipeline.py   train ensemble · walk-forward backtest · daily signals
                          │
                      server.py        Flask API · portfolio DB · scheduler
                          │
                        web/           browser UI (Lightweight Charts)
```

1. **Features:** 35 price, volume, trend, volatility, 52-week-range, market-breadth and cross-sectional features per stock per day.
2. **Model:** an ensemble of three scikit-learn gradient-boosting models (20-day regressor, top-quintile classifier, 10-day regressor) that predict *market-relative* returns. Their within-day ranks are averaged into one score.
3. **Strategy:** hold the **top 3** stocks, re-checked every 2 weeks. Sell a holding when its rank drops below 30th. Go to cash when 40% or fewer of liquid stocks are above their 50-day average.
4. **Continuous learning:** after each close the model retrains on all data. Before that, it compares 3 tree settings on the newest ~4 months it hasn't trained on, and switches only when one is clearly better. History is logged to `data/out/model_history.json`. The full backtest reruns weekly.

**Why 3 stocks?** 54 strategy variants were tested: the best was chosen on 2016–2020, then checked on 2021–2026. Holding 3 stocks re-checked every 2 weeks beat holding 5 in both periods. The most aggressive variant (1 stock, weekly) looked best early but lost 10.6% a year later on.

---

## Backtest results

Walk-forward over 2016 → Oct 2026. Each year is predicted by a model trained only on earlier years, trades fill at the **next day's open**, and every trade pays 0.5% slippage, ~0.4% commission per side and 7.5% capital-gains tax.

| | CAGR 2016–2026 | CAGR 2021–2026 | Worst fall |
|---|---|---|---|
| Model (top 3, market filter) | 6.4% | 9.0% | −41.7% |
| Buy and hold (equal weight) | 17.9% | 7.8% | −48.2% |

- The **ranking is real**: the top-scored decile averages +2.25% over the next 20 days, against −0.09% for the bottom decile (IC 0.093, *t* = 6.2).
- But **costs eat most of the edge**: over the full period the strategy loses to holding the market.
- Since 2021, with 100+ liquid stocks, it slightly beat the market and fell less in bad years. The difference is not statistically significant.

The model cannot see news, NRB policy, dividends or manipulation. Past results do not guarantee the future. Full details are in [docs/RESEARCH.md](docs/RESEARCH.md).

---

## Data sources

| Source | Provides |
|---|---|
| [`Aabishkar2/nepse-data`](https://github.com/Aabishkar2/nepse-data) | Full daily history since 2011 (from ShareSansar, updated daily) |
| [NEPSE official](https://www.nepalstock.com.np) via [`nepse-client`](https://pypi.org/project/nepse-client/) | Live and today's prices, NEPSE index, sectors, missing days (Python 3.11+) |
| ShareSansar *today-share-price* | Backup for today's closing prices |

Each source is optional. If one fails, the others keep working, and the status bar shows which are up.

---

## Project structure

```
.
├── engine/
│   ├── features.py      corporate-action adjustment and feature engineering
│   ├── model.py         fee/tax rules and base settings
│   ├── pipeline.py      training, walk-forward backtest, signals, daily records
│   └── sources.py       GitHub / NEPSE / ShareSansar downloaders
├── web/                 browser UI (HTML, CSS, JS, vendored Lightweight Charts)
├── research/            scripts that reproduce docs/RESEARCH.md
├── docs/RESEARCH.md     full methodology and results
├── server.py            Flask server, portfolio database, scheduler
├── start.sh             one-command setup and launch
├── install_autostart.sh optional macOS login item
└── requirements.txt
```

`data/` is created on first run. It holds prices, the trained model, outputs, logs and your portfolio database, and it is **git-ignored**.

---

## Command-line usage

```bash
source .venv/bin/activate
python -m engine.pipeline              # fetch and analyse now
python -m engine.pipeline --retrain    # force model retraining and backtest
python -m engine.pipeline --no-fetch   # re-analyse without downloading
python -m research.reproduce           # regenerate the research numbers
```

---

## Privacy

Everything runs locally on `localhost`. There is no account, no cloud and no telemetry. Your trades, settings, notes and logs live in `data/` (mainly `data/portfolio.db`), which is excluded from git.

**Back up `data/portfolio.db` regularly.** It is the only copy of your portfolio.

---

## Research write-up

[**docs/RESEARCH.md**](docs/RESEARCH.md) is the full study behind the model: data cleaning, the bonus/rights adjustment, features, walk-forward design, results, robustness checks (random ranking, number of holdings, costs, tree settings, feature importance) and limitations.

---

## License

Released under the [MIT License](LICENSE).

Bundles [TradingView Lightweight Charts™](https://github.com/tradingview/lightweight-charts) v4.2.0 under the Apache License 2.0 (see the header of `web/vendor/lightweight-charts.standalone.production.js`). Price data belongs to its respective providers.

---

## Author

**Prashant Bhandari** · [digistartnp@gmail.com](mailto:digistartnp@gmail.com) · [@prashantbhandary](https://github.com/prashantbhandary)

Issues and suggestions are welcome.
