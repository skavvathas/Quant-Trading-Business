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
