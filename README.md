# NEPSE Quant Desk

**A private ML trading assistant for the Nepal Stock Exchange that runs on your own computer.**

![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![License: MIT](https://img.shields.io/badge/license-MIT-green)

🌐 **[Live dashboard & research report →](https://prashantbhandary.github.io/nepse-quant-desk/)**

---

## What it does

Every trading day it:

- 📥 downloads prices for ~370 NEPSE companies
- 🧠 ranks every stock with a machine-learning model
- 📋 tells you **what to buy, what to sell**, and whether the market is safe
- 💰 tracks your portfolio with **real profit after fees and tax**

## Quick start

```bash
git clone https://github.com/prashantbhandary/nepse-quant-desk.git
cd nepse-quant-desk
./start.sh
```

Open **http://localhost:8050**. The first run takes about 5 minutes; after that it starts in seconds.

> Needs Python 3.11+. On macOS, run `./install_autostart.sh` to start it automatically at login.

## Does it work?

Tested honestly: each year is predicted by a model that has only seen earlier years, and every trade pays real costs.

| | 2016–2026 | 2021–2026 |
|---|---|---|
| **Model** (top 3 stocks) | 6.4% / yr | 9.0% / yr |
| **Buy & hold** the market | 17.9% / yr | 7.8% / yr |

**In short:** the model is good at *ranking* stocks, but trading costs eat most of that edge. Use it as a guide, not a money machine. → [Full research](docs/RESEARCH.md)

> ⚠️ Not financial advice.

## Privacy

Everything runs on `localhost`, with no account, cloud or tracking. Your trades live in `data/portfolio.db`, which is **never committed to git**. Back it up.

## Project layout

```
engine/      data download, features, model, backtest
web/         browser interface
server.py    local server and portfolio database
research/    scripts that reproduce the research numbers
docs/        research report and the GitHub Pages site
```

## License & author

[MIT](LICENSE) © 2026 **Prashant Bhandari** · [santhprashant@gmail.com](mailto:santhprashant@gmail.com)

Price data: [Aabishkar2/nepse-data](https://github.com/Aabishkar2/nepse-data) and [nepalstock.com.np](https://www.nepalstock.com.np). Charts: [Lightweight Charts](https://github.com/tradingview/lightweight-charts) (Apache 2.0).
