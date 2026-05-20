# ORB Strategy — Opening Range Breakout

Based on *"A Profitable Day Trading Strategy For The U.S. Equity Market"*
(Zarattini, Barbon, Aziz — Swiss Finance Institute N°24-98).

Trades the first 5-minute breakout of the day on the top 20 Stocks in Play,
filtered by Relative Volume. Stop loss at 10% ATR, profit target at EOD.

## Key Terms

| Term | Definition |
|---|---|
| **Opening Range (OR)** | The high and low formed by the very first 5-minute candle after 9:30 AM ET. |
| **ORB Signal** | A break above the OR high → LONG; a break below the OR low → SHORT. |
| **ATR** (Average True Range) | 14-day average of each day's full price range in dollars — measures a stock's typical daily volatility. |
| **RelVol** (Relative Volume) | Today's opening candle volume ÷ 14-day average opening candle volume; values > 1 mean unusual activity. |
| **Stocks in Play** | The top 20 symbols ranked by RelVol each morning — high activity means a catalyst is present and the move is more likely to follow through. |
| **R** (Risk Unit) | The dollar risk per share on entry (entry price − stop price); all P&L is expressed as multiples of R so trades are comparable regardless of price. |
| **Stop Loss** | A resting sell/buy order placed at entry ± 10% × ATR; it exits the trade automatically if price reverses. |
| **EOD Close** | All open positions are force-closed at 3:50 PM ET to avoid overnight gap risk. |
| **RelVol Filter** | Only symbols with RelVol ≥ 1.0 (at least 100% of their normal opening volume) are traded. |

## Architecture

```
strategies/orb/
  orb_config.py   — All ORB parameters (universe size, filters, risk)
  strategy.py     — Core logic: filters, signals, position sizing
  data_fetcher.py — Fetch top 800 symbols + download bars from Alpaca
  backtest.py     — Simulation engine (base filters → RelVol → top 20 → trades)
  universe.py     — Overnight universe builder (for live trading)
  scanner.py      — Morning 9:35 AM scan (for live trading)
  executor.py     — Alpaca order execution (stop orders, stop loss, EOD close)
```

## Data

All downloaded data is stored in **`data/bars_cache.db`** (SQLite).

| What | Where | Size |
|---|---|---|
| Daily bars (one file per symbol) | `data/bars/daily/AAPL.parquet` … | ~50 MB total |
| 5-min bars (one file per symbol) | `data/bars/5min/AAPL.parquet` … | ~300–500 MB total |
| Top 800 symbol list | `data/orb_universe.json` | ~20 KB |
| Overnight watchlist (live trading) | `data/orb_watchlist.json` | ~50 KB |

Parquet uses Snappy compression — roughly **10× smaller** than SQLite for the same data.
All `data/bars/` files are gitignored.

## Running the ORB Backtest

### Step 1 — Download data (run once, takes time)

```bash
# Download top 800 symbols + all bars for default range (2022–2024)
python run_orb_backtest.py --download-only

# Custom date range
python run_orb_backtest.py --download-only --start 2022-01-01 --end 2024-12-31

# Smaller universe for faster download
python run_orb_backtest.py --download-only --n-symbols 200
```

Data is cached in `data/bars_cache.db`. Re-running `--download-only` only
fetches symbols/dates not already cached — safe to run again if interrupted.

### Step 2 — Run the backtest (fast, uses cached data)

```bash
# Default range (2022–2024), $25,000 starting capital
python run_orb_backtest.py --skip-download

# Custom range and capital
python run_orb_backtest.py --skip-download --start 2023-01-01 --end 2024-12-31 --capital 50000
```

### All options

```bash
python run_orb_backtest.py [OPTIONS]

  --start          Backtest start date       default: 2022-01-01
  --end            Backtest end date         default: 2024-12-31
  --capital        Starting capital (USD)    default: 25000
  --n-symbols      Universe size             default: 800
  --download-only  Download data then stop
  --skip-download  Skip download, backtest only
```

### One-shot (download + backtest in one command)

```bash
python run_orb_backtest.py
```

## ORB Output Files

| File | Contents |
|---|---|
| `outputs/orb_backtest_trades.csv` | Every trade: symbol, direction, entry/exit price, P&L in R and $ |
| `outputs/orb_backtest_equity.csv` | Daily capital curve |
| `outputs/orb_backtest_metrics.csv` | Total return, CAGR, Sharpe, MDD, win rate |
| `outputs/orb_orders.csv` | Live trading: every Alpaca order submitted |
| `outputs/orb_trades.csv` | Live trading: every closed trade |

## ORB Strategy Parameters

All in `strategies/orb/orb_config.py`:

```python
LOOKBACK_DAYS   = 14      # days for ATR + avg volume calculation
MIN_PRICE       = 5.0     # opening price filter
MIN_AVG_VOLUME  = 1_000_000  # 14-day avg daily volume filter
MIN_ATR         = 0.50    # 14-day ATR filter
MIN_RELVOL      = 1.0     # 100% — today's OR volume >= 14-day avg
TOP_N           = 20      # max simultaneous positions
ATR_STOP_PCT    = 0.10    # stop loss = 10% of ATR from entry
RISK_PER_TRADE  = 0.01    # risk 1% of capital per trade
MAX_LEVERAGE    = 4.0     # FINRA maximum
```
