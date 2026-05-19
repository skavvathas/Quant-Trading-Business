# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (use the .venv virtualenv)
pip install -r requirements.txt

# Copy env template and fill in Alpaca paper API keys
cp .env.example .env

# Launch the Strategy Hub (landing page → navigate to ORB dashboard)
streamlit run app.py

# Start live ORB paper trading loop (or use the Start Trading button in the dashboard)
python orb_main.py

# Run ORB backtest
python strategies/orb/backtest.py
```

No test suite or linter is configured. The project uses Python 3.14+ (CPython); use `.venv/` for the active virtualenv.

## Architecture

The system is an ORB (Opening Range Breakout) intraday trading pipeline with these entry points:

```
app.py              → Streamlit landing page (strategy hub)
pages/
  ORB_Dashboard.py  → ORB live dashboard (delegates to orb_dashboard.py)
orb_dashboard.py    → ORB dashboard implementation (also runnable standalone)
orb_main.py         → ORB live trading loop (Alpaca paper/live)
strategies/orb/     → Strategy logic, scanner, executor, backtest
```

### Data flow (live loop)

```
data_manager.py
  fetch_5min_bars()   → Alpaca (primary) or yfinance (fallback) → SQLite cache
  fetch_daily_bars()  → same pattern, 60-min TTL
  get_vix()           → yfinance ^VIX, 15-min in-process cache

signals.py
  RegimeClassifier    → annualized realized vol → low/medium/high_vol label
  VIXGate             → VIX → market regime + position multiplier
  MeanReversionSignal → Z-score on last 20 5-min closes → direction (±1 or 0)

position_sizer.py   → BASE_POSITION_SIZE × stock_mult × vix_mult → final shares

risk_manager.py     → market hours gate, kill switch (daily drawdown), position limits

executor.py
  OrderManager        → Alpaca order submit + fill reconciliation + orders.csv
  ExitManager         → TP / SL / EOD / max-hold checks → execute_exit() → trades.csv

session_manager.py  → reads/writes data/session_config.json (strategy + symbol list)
```

### Dashboard ↔ main.py communication

`dashboard.py` saves the user's selected strategy and symbols to `data/session_config.json` via `session_manager.save_session_config()`. The live loop re-reads this file at the start of **every** 5-minute iteration via `get_active_symbols()`, so changes take effect without restarting `main.py`.

### Single source of truth: `config.py`

All strategy thresholds, file paths, Alpaca credentials (via `.env`), universe, and regime multipliers live in `config.py`. Changing a parameter here propagates everywhere — no other file needs editing.

### Paper vs. live trading

`config.PAPER_TRADING = True` and `ALPACA_BASE_URL` default to the Alpaca paper endpoint. Without any API key, `executor.py` generates fake `SIM-*` order IDs and `data_manager.py` falls back entirely to yfinance — useful for local development.

### Output files

| Path | Written by |
|---|---|
| `outputs/signals.csv` | main.py (each signal) |
| `outputs/orders.csv` | executor.OrderManager |
| `outputs/trades.csv` | executor.ExitManager |
| `outputs/backtest_trades.csv` | backtest.py |
| `outputs/backtest_report.csv` | backtest.py |
| `outputs/backtest_charts.png` | backtest.py |
| `logs/trading.log` | main.py logging |
| `data/bars_cache.db` | data_manager (SQLite, WAL mode) |
| `data/session_config.json` | session_manager (dashboard config) |
