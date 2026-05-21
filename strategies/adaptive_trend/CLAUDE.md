# AdaptiveTrend Strategy

Systematic trend-following applied to **crypto spot markets**, based on:
> *"Systematic Trend-Following in Crypto Markets"* (2025)

Trades 6-hour bars on 20 liquid Alpaca crypto pairs (USD). Goes long the top-momentum coins and short the worst-momentum coins, filtered by a trailing Sharpe ratio gate. Portfolio rebalances monthly.

## Strategy Logic

1. **Momentum signal** — `MOM = (P_t - P_{t-20}) / P_{t-20}` over 20 × 6h bars (~5 days)
2. **Long universe** — top-10 coins by 30-day dollar volume with MOM > 2% and trailing Sharpe > 1.3
3. **Short universe** — bottom-5 by momentum with |MOM| > 2% and trailing Sharpe > 1.7
4. **Allocation** — 70% of capital to longs, 30% to shorts (equal-weighted within each leg)
5. **Trailing stop** — entry stop set at `price − 2.5 × ATR14` (LONG) or `price + 2.5 × ATR14` (SHORT); updated every bar
6. **Rebalance** — monthly: exit positions no longer in universe, enter new ones

## Key Parameters (`adaptive_trend_config.py`)

| Parameter | Value | Meaning |
|---|---|---|
| `LOOKBACK_BARS` | 20 | Momentum lookback (20 × 6h ≈ 5 days) |
| `ENTRY_THRESHOLD` | 2% | Min momentum to enter long |
| `SHORT_THRESHOLD` | 2% | Min \|momentum\| to enter short |
| `BAR_INTERVAL_H` | 6 | Bar width in hours |
| `ALPHA` | 2.5 | ATR multiplier for trailing stop |
| `ATR_PERIOD` | 14 | ATR window (bars) |
| `LAMBDA_LONG` | 0.70 | Fraction of capital for long leg |
| `LAMBDA_SHORT` | 0.30 | Fraction of capital for short leg |
| `K_LONG` | 10 | Top-N coins eligible for long |
| `K_SHORT` | 5 | Bottom-N coins eligible for short |
| `GAMMA_LONG` | 1.3 | Min trailing Sharpe to enter long |
| `GAMMA_SHORT` | 1.7 | Min trailing Sharpe to enter short |
| `REBALANCE_FREQ` | monthly | Portfolio rebalancing frequency |
| `TAKER_FEE_BPS` | 25 | Alpaca crypto taker fee (0.25%) |

## Universe

20 liquid Alpaca crypto USD pairs: BTC, ETH, SOL, AVAX, DOT, LINK, UNI, BCH, LTC, AAVE, BAT, GRT, FIL, ARB, LDO, CRV, SUSHI, YFI, XTZ, POL.

## File Structure

```
strategies/adaptive_trend/
├── adaptive_trend_config.py   — all parameters + output paths
├── strategy.py                — Signal, Position, compute_momentum, compute_atr, trailing stop
├── scanner.py                 — screens universe, ranks by momentum + Sharpe, returns signals
├── universe.py                — loads crypto universe, filters by dollar volume
├── backtest.py                — walk-forward simulation over 6h bars
└── executor.py                — Alpaca crypto order management
```

## Running

```bash
# Backtest
python -m strategies.adaptive_trend.backtest

# Scan for live signals
python -m strategies.adaptive_trend.scanner
```

## Output Files

| Path | Contents |
|---|---|
| `data/bars/crypto_6h/` | Cached 6-hour OHLCV bars (Alpaca) |
| `data/adaptive_trend_scan.json` | Latest scan results |
| `outputs/at_backtest_trades.csv` | Per-trade backtest log |
| `outputs/at_backtest_report.csv` | Summary metrics |

## Data

6-hour bars fetched from Alpaca Crypto API. `FETCH_DAYS = 120` calendar days of history pulled per symbol. Bars cached locally; re-fetch only when stale.

## Key Differences vs. ORB strategies

| | adaptive_trend | orb / orb_qqq |
|---|---|---|
| Asset class | Crypto spot | US equities |
| Timeframe | 6-hour bars, multi-day holds | 5-min bars, intraday only |
| Direction | Long + short portfolio | Single direction per day |
| Rebalance | Monthly | Daily (each session) |
| Universe | 20 crypto pairs | 4,000 stocks / QQQ + TQQQ |
