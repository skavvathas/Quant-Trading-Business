# Regime-Based Mean Reversion — Alpaca Trading System

A modular, production-ready intraday mean reversion system that gates trades
by stock-level volatility regime and VIX-based market regime.

## Architecture

```
config.py          — All strategy parameters and API credentials
data_manager.py    — Alpaca + yfinance data fetching, SQLite caching
signals.py         — RegimeClassifier, VIXGate, MeanReversionSignal
position_sizer.py  — Share count with regime × VIX multipliers
risk_manager.py    — Market hours, position limits, kill switch
executor.py        — Order submission, fill tracking, exit logic
main.py            — 5-minute live trading loop
backtest.py        — Vectorized vectorized backtester (2024-2025)
dashboard.py       — Streamlit real-time monitor
```

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Copy and fill in your Alpaca paper keys
cp .env.example .env

# 3. Run the backtest (no API key required — uses yfinance)
python backtest.py

# 4. Start paper trading
python main.py

# 5. Launch dashboard (in a second terminal)
streamlit run dashboard.py
```

## Strategy Logic

### 1. Stock Regime (per symbol, daily)
| Realized Vol | Regime | Share Mult |
|---|---|---|
| < 15% | low_vol | 1.0× |
| 15–30% | medium_vol | 0.6× |
| ≥ 30% | high_vol | 0× (skip) |

### 2. Market Regime (VIX gate)
| VIX | Regime | Size Mult |
|---|---|---|
| < 15 | low_vol | 1.0× |
| 15–25 | medium_vol | 0.8× |
| > 25 | high_vol | 0.5× |

### 3. Mean Reversion Signal (5-min bars)
```
Z = (close - SMA_20) / STD_20
LONG  if Z < -2.0
SHORT if Z >  2.0
```

### 4. Position Sizing
```
final_size = floor(BASE_SIZE × stock_mult × vix_mult)
BASE_SIZE = 5 shares
```

### 5. Exit Rules
| Rule | Threshold |
|---|---|
| Take Profit | +2.0% |
| Stop Loss | −1.0% |
| EOD Close | 3:50 PM ET |
| Max Hold | 3 days |

## Output Files

| File | Contents |
|---|---|
| `outputs/signals.csv` | Every signal generated |
| `outputs/orders.csv` | Every order submitted |
| `outputs/trades.csv` | Every closed trade with P&L |
| `outputs/backtest_trades.csv` | Backtest trade log |
| `outputs/backtest_report.csv` | Per-symbol metrics |
| `outputs/backtest_charts.png` | Equity curve + charts |
| `logs/trading.log` | Structured runtime log |

## Configuration

All thresholds in `config.py`:

```python
Z_SCORE_ENTRY_THRESHOLD = 2.0
TAKE_PROFIT_PCT = 0.02
STOP_LOSS_PCT = -0.01
REALIZED_VOL_LOW_THRESHOLD = 0.15
REALIZED_VOL_HIGH_THRESHOLD = 0.30
VIX_LOW_THRESHOLD = 15
VIX_HIGH_THRESHOLD = 25
BASE_POSITION_SIZE = 5
LOOKBACK_BARS = 20
```

## Implementation Phases

- **Phase 1** ✅ config + data_manager + signals
- **Phase 2** ✅ backtest on 2024–2025 data
- **Phase 3** ✅ position_sizer + risk_manager + executor + main loop
- **Phase 4** ✅ Streamlit dashboard
- **Phase 5** Refine thresholds → go live ($500–1000/trade)

---

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
