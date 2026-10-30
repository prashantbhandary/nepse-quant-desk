# Machine-Learning Stock Ranking on the Nepal Stock Exchange

**A walk-forward evaluation with realistic trading costs**

**Prashant Bhandari** · [digistartnp@gmail.com](mailto:digistartnp@gmail.com) · October 2026

> Every number in this document can be regenerated with `python -m research.reproduce` (see [Reproducing the results](#reproducing-the-results)).

---

## Contents

1. [Summary](#summary)
2. [Background](#background)
3. [Data](#data)
4. [Method](#method)
5. [Results](#results)
6. [Robustness checks](#robustness-checks)
7. [Limitations](#limitations)
8. [Conclusion](#conclusion)
9. [Reproducing the results](#reproducing-the-results)
10. [References](#references)

---

## Summary

This study asks whether a gradient-boosted tree ensemble, trained on 35 price- and volume-based features, can rank stocks on the Nepal Stock Exchange (NEPSE). NEPSE is a small frontier market with thin liquidity and a daily price limit.

- **Data:** daily prices for 369 listed companies, 2011 to October 2026.
- **Test design:** an expanding-window walk-forward. Each year from 2016 to 2026 is predicted by a model trained only on earlier data, with a 40-day embargo.
- **Execution:** trades fill at the next day's opening price and pay brokerage commission, slippage and capital-gains tax.

**Key findings**

| Question | Answer |
|---|---|
| Does the model rank stocks well? | **Yes.** Mean daily Spearman IC with 20-day forward returns is **0.093** (Newey–West *t* = 6.2). Forward returns rise steadily across score deciles. The top decile beats the bottom decile by **2.55% per 20 days** (*t* = 5.1). |
| Is it better than simple momentum? | **Yes.** A 60-day momentum ranking has an IC of only 0.032 (*t* = 1.7). |
| Does a strategy built on it beat the market? | **No, not over the full period.** A top-3 portfolio, re-balanced every 10 trading days with a market-breadth filter, earned **6.4% a year** over 2016–2026. Equal-weighted buy-and-hold earned **17.9%**. |
| What about recent years? | Since 2021 it earned **9.0% vs 7.8%** a year with a smaller worst fall, but the difference is **not statistically significant**. |

Results depend heavily on trading costs and on how many stocks are held. Rank statistics on their own overstate what an investor can actually earn on NEPSE.

---

## Background

**Market efficiency and momentum.** The efficient-market hypothesis (Fama, 1970) says past prices should not forecast future returns once costs are counted. Momentum, where recent winners keep winning, is the best-known exception (Jegadeesh & Titman, 1993), and it shows up across international markets (Rouwenhorst, 1998). Efficiency measures behave differently in emerging markets (Griffin et al., 2010), so frontier markets need their own studies.

**Machine learning in asset pricing.** Tree ensembles and neural networks roughly double the predictive power of linear models for US stocks (Gu et al., 2020). Large pre-cost returns from boosted trees on the S&P 500 mostly disappear after costs (Krauss et al., 2017). Histogram-based gradient boosting (Friedman, 2001; Ke et al., 2017) is now a standard tool for tabular financial data.

**Backtest overfitting.** When many strategy variants are tried, the best one in sample usually disappoints out of sample (Bailey et al., 2014). New return predictors should face higher significance thresholds (Harvey et al., 2016). Labels that overlap in time need purged and embargoed validation (López de Prado, 2018), and this study follows that advice.

**Why this study.** Many NEPSE studies report high returns. However, they often trade at the same closing price the signal was computed from, ignore costs and taxes, or tune on the evaluation data. Most also forecast the index rather than rank individual stocks. This study adds a stock-level, cost-aware evaluation over several market cycles.

---

## Data

### Sources

| Source | Used for |
|---|---|
| [`Aabishkar2/nepse-data`](https://github.com/Aabishkar2/nepse-data) | Daily OHLC, traded quantity and amount since 2011 |
| [nepalstock.com.np](https://www.nepalstock.com.np) (official) | Cross-checking recent days |

The panel has **369 symbols and 513,599 stock-days**, from 2 January 2011 to 29 October 2026. Only ordinary shares are kept. Mutual funds, debentures and promoter shares are removed.

### Adjusting for bonus and rights issues

Bonus and rights issues cause large mechanical price drops on book-closure dates, and the source data does not record them. The adjustment is inferred from the daily price limit, which was ±10% until 20 April 2026 and ±15% after:

1. **From about 2019:** the reported % change is measured against an *adjusted* previous close. If the raw close-to-close change is more than 3 percentage points below the reported change, the ratio of the two implied previous closes gives the exact factor.
2. **Earlier years:** any overnight fall larger than the price limit counts as a corporate action, and that day is treated as flat.

Only factors between 0.2 and 0.97 are accepted. Earlier prices are multiplied by the cumulative factor, and volumes are divided by it.

### Tradable universe

A stock is tradable on a day if **all** of these hold:

- its 20-day average daily turnover is at least Rs 5 million;
- its price is at least Rs 100;
- it has a 200-day moving average;
- it traded within the last 15 calendar days.

**Average tradable stocks per day**

| 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|
| 37 | 34 | 18 | 19 | 69 | 126 | 89 | 84 | 189 | 197 | 165 |

The universe was tiny before 2020, so results for 2016–2020 and 2021–2026 are reported separately throughout.

---

## Method

### Features (35)

All features use only information available at that day's close.

| Group | Features |
|---|---|
| Returns | 5, 10, 20, 60, 120-day returns |
| Trend | Price vs 10/20/50/200-day MAs; slopes of 20 and 50-day MAs; MACD and histogram |
| Oscillators | 14-day RSI; Bollinger %B and width; share of up days in last 10 |
| Volatility | 14-day ATR as % of price; 20-day std of daily returns |
| Volume & liquidity | Volume vs 20-day average; 5 vs 60-day volume; log 20-day turnover |
| 52-week range | Distance from 52-week high and low; 20-day breakout flag |
| Market | Equal-weighted market return (5, 20 days); market vs its 50-day MA; breadth above 50 and 200-day MAs |
| Cross-sectional | 20 and 60-day return vs market; daily percentile ranks of 20/60-day return and relative volume |

### Labels and model

Labels are **market-relative**: `x = r(h) − mean r(h)` over that day's tradable stocks. The ensemble combines three scikit-learn `HistGradientBoosting` models:

1. a regressor on 20-day excess return (absolute-error loss, label clipped to ±30%);
2. a classifier for "top quintile of the day" on 20-day excess return;
3. a regressor on 10-day excess return.

Each output becomes a within-day percentile rank, and the final **score** is the average of the three. The tree settings were fixed in advance: 250 iterations, learning rate 0.04, 31 leaves, at least 300 samples per leaf, L2 = 1.0.

### Walk-forward design

For each test year *y* = 2016 … 2026, the model trains on all tradable stock-days dated more than **40 calendar days before 1 January *y***. This embargo means no 20-day label overlaps the test year. A year is skipped if fewer than 10,000 training rows exist. That happens for 2016, so the strategy holds cash that year.

### Trading rules and costs

Every **10 trading days**, using the previous close's scores, trading at the **next open**:

- sell any holding whose rank has fallen below **30th**;
- buy up to **N = 3** top-ranked stocks, equal-weighted;
- **market filter:** if 40% or fewer of liquid stocks are above their 50-day MA, sell everything and hold cash.

| Cost | Value |
|---|---|
| Commission and fees | 0.4% per side |
| Slippage | 0.5% per side |
| Capital-gains tax | 7.5% of positive gains |
| Daily return clip | ±16% (removes data errors) |

### Benchmark and statistics

- **Benchmark:** equal-weight every tradable stock on the first trading day of each year and hold to year end. It pays no costs, which slightly favours the benchmark.
- **Ranking quality:** daily Spearman IC between score and the 20-day return from the next open, with Newey–West *t*-statistics (20 lags).
- **Excess return:** moving-block bootstrap with 20-day blocks and 2,000 resamples.

---

## Results

### Ranking quality

Over 2,131 test days the mean IC is **0.093** (sd 0.225). It is positive on 67% of days and in every year except 2020, when the post-COVID rally lifted almost everything.

| Year | Model IC | 60-day momentum IC |
|---|---|---|
| 2017 | 0.174 | 0.160 |
| 2018 | 0.059 | −0.039 |
| 2019 | 0.117 | 0.057 |
| 2020 | −0.001 | 0.053 |
| 2021 | 0.011 | 0.034 |
| 2022 | 0.063 | −0.042 |
| 2023 | 0.097 | 0.095 |
| 2024 | 0.172 | −0.002 |
| 2025 | 0.071 | −0.034 |
| 2026 | 0.161 | 0.040 |

**Average 20-day return from next open, by score decile**

| Decile | 1 (low) | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 (high) |
|---|---|---|---|---|---|---|---|---|---|---|
| Return % | −0.09 | −0.07 | 0.27 | 0.43 | 0.68 | 0.89 | 1.18 | 1.51 | 1.79 | 2.25 |

### Strategy performance

| Strategy | CAGR 16–20 | CAGR 21–26 | CAGR 16–26 | MDD 16–26 | Vol | Sharpe |
|---|---|---|---|---|---|---|
| **Model, top 3, market filter** | 2.5% | 9.0% | 6.4% | −41.7% | 22.9% | 0.40 |
| Model, top 3, no filter | 5.1% | −6.0% | −0.6% | −63.4% | 30.0% | 0.13 |
| Momentum, top 3, market filter | 6.9% | 1.8% | 4.6% | −53.1% | 25.1% | 0.31 |
| Buy and hold (equal weight) | 30.0% | 7.8% | 17.9% | −48.2% | 23.5% | 0.85 |

- **Full period:** the model trails the benchmark (6.4% vs 17.9%). Part of the gap is 2016, when the model sat in cash while the market rose 87%. Without 2016: 7.1% vs 12.2%.
- **2016–2020:** with only 18–69 stocks a day, the strategy lagged badly. Bootstrap 95% CI for excess return: [−42.7%, −6.3%].
- **2021–2026:** 9.0% vs 7.8% with a shallower worst fall (−37.1% vs −48.2%). In 2022 it lost 11.9% vs 19.9%. But the CI is [−17.9%, +22.0%], so the difference cannot be told apart from zero.
- **The market filter matters.** Without it, the same ranking returns −0.6% a year with a −63.4% worst fall.

**Calendar-year returns (%)**

| Year | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Model | 0.0 | −13.6 | −10.1 | −1.2 | 47.3 | 60.5 | −11.9 | −1.9 | 34.2 | −1.2 | −5.5 |
| Buy & hold | 87.3 | 8.5 | −16.0 | −0.6 | 118.5 | 46.5 | −19.9 | 1.2 | 38.6 | −1.7 | −1.3 |

**Trades.** 177 round trips, median holding period 19 days. Only **42.9%** were profitable after costs, and the median trade lost 2.2%. The average trade still gained 1.8%, because winners (+13.8%) were about twice the size of losers (−7.2%). So results depend on a few big winners.

### Why a good ranking does not become profit

1. **Costs are large relative to the edge.** The top decile beats the average stock by ~1.4 points per 20 days, while a round trip costs ~1.8% plus tax, about 16 times a year.
2. **Three stocks is noisy.** Stock-specific noise swamps the small average edge.
3. **The filter has a cost.** It avoids the worst crashes but misses the sharp early part of rallies such as 2020.

---

## Robustness checks

**Random ranking.** Replacing the score with random numbers 100 times, with all other rules unchanged, gives a median of **−6.1%** a year (5th–95th percentile: −10.5% to +2.1%). The model's 6.4% beats all 100 runs, so the ranking adds real value beyond the rules and filter.

**Number of holdings**

| N | 2016–20 | 2021–26 | 2016–26 | MDD 2016–26 |
|---|---|---|---|---|
| 1 | −0.3% | 10.7% | 6.4% | −55.8% |
| 2 | 4.2% | 17.5% | 11.7% | −41.8% |
| **3** | 2.5% | 9.0% | 6.4% | −41.7% |
| 5 | −2.9% | 9.5% | 4.1% | −44.0% |
| 10 | −0.2% | 9.1% | 5.1% | −39.5% |
| Buy & hold | 30.0% | 7.8% | 17.9% | −48.2% |

No N beats the benchmark over the full period. Picking N = 2 after seeing this table would be exactly the selection bias Bailey et al. (2014) warn about. In an earlier search over 54 variants, the best 2016–2020 setting (1 stock, weekly) **lost 10.6% a year** in 2021–2026.

**Trading costs (2016–2026)**

| Cost per side | CAGR | MDD | Sharpe |
|---|---|---|---|
| 0.00% | 17.1% | −29.7% | 0.84 |
| 0.45% | 11.7% | −34.4% | 0.62 |
| **0.90% (baseline)** | 6.4% | −41.7% | 0.40 |
| 1.35% | 1.4% | −48.4% | 0.18 |
| 1.80% | −3.3% | −55.9% | −0.03 |

Each extra 0.45 points of cost per side removes about 5 points of annual return.

**Tree settings**

| Setting | IC | 2016–20 | 2021–26 | 2016–26 | MDD |
|---|---|---|---|---|---|
| **Base (pre-specified)** | 0.093 | 2.5% | 9.0% | 6.4% | −41.7% |
| Steady (smaller trees) | 0.093 | 3.1% | 8.4% | 6.3% | −40.3% |
| Sharp (larger trees) | 0.093 | 2.3% | 18.2% | 11.2% | −31.8% |

All three settings rank stocks equally well, so the gap in returns reflects how sensitive a 3-stock portfolio is to which stocks it happens to pick, not a better model. The pre-specified setting is kept.

**Feature importance** (permutation, model trained to 2020 and tested on 2021–2026; drop in IC from a base of 0.064)

| Feature | IC drop |
|---|---|
| `dist_lo52`: distance from 52-week low | 0.015 |
| `log_turnover` | 0.009 |
| `vol20`: 20-day volatility | 0.008 |
| `rank_ret20`: cross-sectional rank of 20-day return | 0.006 |
| `bb_width`: Bollinger band width | 0.004 |

The model leans on where a stock sits in its yearly range, its liquidity and its volatility, more than on classic oscillators like RSI. Market-wide features are the same for every stock on a given day, so shuffling them within a day has no effect. Their job is to condition the other features on the market state.

---

## Limitations

- **Survivorship.** The panel holds companies listed in 2026. Firms delisted or merged earlier, including many 2019–2022 bank mergers, may be missing. This likely biases both strategy and benchmark upward.
- **Corporate actions** are inferred from prices, not official records, and cash dividends are not added back. Everything is measured on price returns only.
- **Execution.** Fills are assumed at open plus slippage, which thin stocks may not allow.
- **Selection.** The trading rules (10-day re-balance, exit at rank 30, 40% breadth) were chosen during development. None was tuned on the full sample, but earlier results had been seen, so some bias remains.

---

## Conclusion

Standard technical, liquidity and market-state features produce a statistically strong out-of-sample ranking of NEPSE stocks (IC ≈ 0.09, with steadily rising decile returns). This suggests NEPSE is not fully weak-form efficient. Yet with realistic next-open execution, commission, slippage and tax, a concentrated long-only strategy built on that ranking **does not beat equal-weighted buy-and-hold over 2016–2026**. Since 2021 its edge is small and not statistically significant.

- **For researchers:** rank statistics and cost-free backtests can greatly overstate what investors earn on NEPSE.
- **For investors:** the ranking may work better as a filter, for example to avoid the lowest-ranked stocks, than as a stand-alone trading system.

**Future work:** add fundamental and dividend data, use broader lower-turnover portfolios, and test on a survivorship-free panel.

---

## Reproducing the results

The price data are public ([`Aabishkar2/nepse-data`](https://github.com/Aabishkar2/nepse-data)). After the app has downloaded data once (`./start.sh`), run from the project root:

```bash
source .venv/bin/activate
python -m research.reproduce   # every table above → research/out/
python -m research.configs     # tree-settings table → research/out/configs.json
```

---

## References

- Bailey, D. H., Borwein, J. M., López de Prado, M., & Zhu, Q. J. (2014). Pseudo-mathematics and financial charlatanism: The effects of backtest overfitting on out-of-sample performance. *Notices of the AMS*, 61(5), 458–471.
- Breiman, L. (2001). Random forests. *Machine Learning*, 45(1), 5–32.
- Fama, E. F. (1970). Efficient capital markets: A review of theory and empirical work. *Journal of Finance*, 25(2), 383–417.
- Friedman, J. H. (2001). Greedy function approximation: A gradient boosting machine. *Annals of Statistics*, 29(5), 1189–1232.
- Griffin, J. M., Kelly, P. J., & Nardari, F. (2010). Do market efficiency measures yield correct inferences? *Review of Financial Studies*, 23(8), 3225–3277.
- Grinold, R. C., & Kahn, R. N. (2000). *Active Portfolio Management* (2nd ed.). McGraw-Hill.
- Gu, S., Kelly, B., & Xiu, D. (2020). Empirical asset pricing via machine learning. *Review of Financial Studies*, 33(5), 2223–2273.
- Harvey, C. R., Liu, Y., & Zhu, H. (2016). … and the cross-section of expected returns. *Review of Financial Studies*, 29(1), 5–68.
- Jegadeesh, N., & Titman, S. (1993). Returns to buying winners and selling losers. *Journal of Finance*, 48(1), 65–91.
- Ke, G., et al. (2017). LightGBM: A highly efficient gradient boosting decision tree. *NeurIPS*, 30.
- Krauss, C., Do, X. A., & Huck, N. (2017). Deep neural networks, gradient-boosted trees, random forests: Statistical arbitrage on the S&P 500. *European Journal of Operational Research*, 259(2), 689–702.
- Künsch, H. R. (1989). The jackknife and the bootstrap for general stationary observations. *Annals of Statistics*, 17(3), 1217–1241.
- López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley.
- Newey, W. K., & West, K. D. (1987). A simple, positive semi-definite, heteroskedasticity and autocorrelation consistent covariance matrix. *Econometrica*, 55(3), 703–708.
- Pedregosa, F., et al. (2011). Scikit-learn: Machine learning in Python. *JMLR*, 12, 2825–2830.
- Rouwenhorst, K. G. (1998). International momentum strategies. *Journal of Finance*, 53(1), 267–284.
- Aabishkar2. *nepse-data: Daily price history of NEPSE-listed companies*. https://github.com/Aabishkar2/nepse-data (accessed October 2026).
- Nepal Stock Exchange Ltd. *Official market data*. https://www.nepalstock.com.np (accessed October 2026).
