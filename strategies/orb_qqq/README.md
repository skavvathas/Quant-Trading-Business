# ORB QQQ / TQQQ Strategy

Implementation of the 5-minute Opening Range Breakout strategy applied to **QQQ** and **TQQQ**, based on:

> *"Can Day Trading Really Be Profitable?"*  
> Carlo Zarattini & Andrew Aziz — Concretum Research / Peak Capital Trading (2023, updated 2025)

---

## Paper Results (2016–2023)

| Strategy | Total Return | Ann. Return | Sharpe | MDD |
|---|---|---|---|---|
| **ORB TQQQ** | **1,484%** | 48% | 1.19 | 28% |
| ORB QQQ | 676% | 33% | 1.13 | 22% |
| Buy & Hold TQQQ | 438% | 27% | 0.69 | 82% |
| Buy & Hold QQQ | 169% | 15% | 0.73 | 36% |

Annualised alpha: **33%** (QQQ), **48%** (TQQQ) — both p < 0.003.  
Beta ≈ 0 in both cases → **returns are uncorrelated with the market**.

The key insight: using TQQQ (3× leveraged) lets a $25k retail account access the same dollar exposure that would require a $300k QQQ account — bypassing the 4× intraday leverage constraint that normally caps retail returns.

---

## Strategy Logic

### Baseline (paper Sections 2–3)

1. Observe the **first 5-minute candle** (9:30–9:35 ET).
2. **Skip** if it is a doji (`open == close`).
3. **Direction**: bullish candle (`close > open`) → LONG; bearish → SHORT.
4. **Entry**: open of the **second** 5-minute candle (9:35 ET).
5. **Stop loss**: low of 1st candle (LONG) / high of 1st candle (SHORT).
6. **Profit target**: entry ± 10 × |entry − stop|, or EOD — whichever is first.

Position sizing formula (exact from paper):
```
Shares = int[ min( A × 0.01 / $R,  4 × A / P ) ]
```
where `A` = account equity, `$R` = |entry − stop|, `P` = entry price.

Parameters: $25k starting capital, 4× max leverage, 1% risk/trade, $0.0005 commission/share.

### Optimised (paper Section 4, Figure 7 peak)

Same entry, but:
- **Stop**: entry ± **5% × 14-day ATR** (tighter than candle high/low)
- **Target**: **EOD only** — no fixed profit target (let profits run)

This produced 9,350% total return ($25k → $6.4M) with 93% annualised alpha in the paper. Note: requires a small account at the current TQQQ price (~$25/share) to avoid stop-execution slippage on the ~$0.08 stop width.

---

## File Structure

```
strategies/orb_qqq/
├── __init__.py           — package docstring
├── orb_qqq_config.py     — all parameters
├── strategy.py           — signal generation + intraday simulation
├── data_fetcher.py       — Alpaca bar download + Parquet cache
├── backtest.py           — walk-forward backtest + CLI
└── README.md             — this file
```

---

## Running the Backtest

### Step 1 — Prerequisites

Make sure your `.env` has valid Alpaca credentials (free tier is sufficient):
```
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
```

### Step 2 — Fetch historical data (run once)

```bash
PYTHONPATH=. python3 strategies/orb_qqq/backtest.py \
    --instrument QQQ \
    --start 2016-01-01 \
    --end 2023-02-17 \
    --fetch
```

This downloads 5-min and daily bars for the chosen instrument into:
```
data/bars/orb_qqq/5min/QQQ.parquet
data/bars/orb_qqq/1day/QQQ.parquet
```

Repeat for TQQQ:
```bash
PYTHONPATH=. python3 strategies/orb_qqq/backtest.py \
    --instrument TQQQ \
    --start 2016-01-01 \
    --end 2023-02-17 \
    --fetch
```

> Note: TQQQ launched in February 2010. Alpaca data goes back to 2016.

### Step 3 — Run the backtest

```bash
# QQQ — both variants (paper period)
python run_orb_qqq_backtest.py

# TQQQ — both variants (paper period)
python run_orb_tqqq_backtest.py

# Out-of-sample: 2024–2026 with 10% ATR stop (validated OOS, saves to _oos_ files)
python run_orb_tqqq_backtest.py --start 2024-01-01 --end 2026-03-31 --tag oos --atr-stop-pct 0.10
python run_orb_qqq_backtest.py  --start 2024-01-01 --end 2026-03-31 --tag oos --atr-stop-pct 0.10

# If data is already downloaded, skip the fetch
python run_orb_tqqq_backtest.py --start 2024-01-01 --end 2026-03-31 --tag oos --atr-stop-pct 0.10 --skip-download

# Specific variant only
python run_orb_tqqq_backtest.py --variants optimised --skip-download

# Custom capital
python run_orb_tqqq_backtest.py --start 2024-01-01 --end 2026-03-31 --equity 50000
```

### Step 4 — Review outputs

Results are written to the `outputs/` directory:

| File | Contents |
|---|---|
| `orbqqq_qqq_baseline_trades.csv` | One row per trade (entry, exit, R, PnL) |
| `orbqqq_qqq_baseline_report.csv` | Summary metrics (Sharpe, MDD, win rate…) |
| `orbqqq_qqq_optimised_trades.csv` | Optimised variant trades |
| `orbqqq_qqq_optimised_report.csv` | Optimised variant metrics |

The **Streamlit dashboard** (`pages/ORB_QQQ_Dashboard.py`) auto-loads these files and displays equity curves and metrics when they exist.

---

## CLI Reference

```
python3 strategies/orb_qqq/backtest.py [options]

  --instrument  QQQ | TQQQ        (default: QQQ)
  --start       YYYY-MM-DD        (default: 2016-01-01)
  --end         YYYY-MM-DD        (default: 2023-02-17)
  --equity      float             (default: 25000)
  --variants    baseline | optimised | both  (default: both)
  --fetch       download bars first
```

---

## Important Caveats

1. **No slippage assumed** — the paper acknowledges this. For small accounts trading QQQ/TQQQ in normal sizes, slippage is negligible. Large accounts will experience meaningful slippage on tight ATR stops.

2. **Optimised variant stop width** — 5% of ATR on TQQQ can be as small as $0.08. At large share sizes this stop will frequently be exceeded by the bid/ask spread alone. This variant is only viable for small accounts.

3. **TQQQ beta decay** — TQQQ rebalances daily to maintain 3× exposure. Buy-and-hold TQQQ suffers from compounding decay ("constant leverage trap") in volatile sideways markets. The ORB strategy avoids this by never holding overnight.

4. **Data coverage** — Alpaca free tier covers back to 2016. The optimised variant's 9,350% result (2016–2023) cannot be reproduced if your data starts later.

5. **Commission model** — Paper uses $0.0005/share. Interactive Brokers Pro Tiered is similar. Commission-free brokers (Robinhood, Webull) may produce slightly better results but have wider spreads.

---

## Module API

```python
from strategies.orb_qqq.backtest import run_backtest
from strategies.orb_qqq.data_fetcher import fetch_bars

# Fetch once
fetch_bars(["QQQ", "TQQQ"], "2016-01-01", "2023-02-17")

# Run baseline
trades, equity_curve, metrics = run_backtest(
    symbol="QQQ", start="2016-01-01", end="2023-02-17",
    variant="baseline", capital=25_000,
)

# Run optimised
trades_opt, equity_opt, metrics_opt = run_backtest(
    symbol="TQQQ", start="2016-01-01", end="2023-02-17",
    variant="optimised", capital=25_000,
)
```
