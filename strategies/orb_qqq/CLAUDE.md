# ORB QQQ / TQQQ Strategy

5-minute Opening Range Breakout applied to **QQQ** and **TQQQ**, based on:
> *"Can Day Trading Really Be Profitable?"* — Zarattini & Aziz, Concretum Research (2023/2025)

**Live trading instrument: TQQQ only** (optimised variant, ATR stop = 10%).

## Strategy Logic

### Baseline
1. Observe the first 5-min candle (9:30–9:35 ET)
2. Skip if doji (`open == close`)
3. Direction: bullish candle → LONG; bearish → SHORT
4. Entry: open of second candle (9:35 ET)
5. Stop: low of 1st candle (LONG) / high (SHORT)
6. Target: entry ± 10 × |entry − stop|, or EOD

### Optimised ← used in live trading
Same entry, but:
- Stop: entry ± `ATR_STOP_PCT × 14-day ATR` (10% for OOS / live)
- Target: EOD only (no fixed profit target)

Position sizing: `Shares = min(equity × 1% / $R,  equity × 4 / price)`

## Key Parameters (`orb_qqq_config.py`)

| Parameter | Value | Meaning |
|---|---|---|
| `ATR_STOP_PCT` | 0.10 | 10% of 14-day ATR as stop (validated OOS 2024-2026) |
| `ATR_PERIOD` | 14 | Trading days for ATR |
| `RISK_PER_TRADE` | 1% | Max account risk if stop is hit |
| `MAX_LEVERAGE` | 4× | FINRA intraday retail cap |
| `TARGET_R` | 10 | Baseline profit target (R-multiples) |
| `COMMISSION` | $0.0005/share | Matches paper |
| `STARTING_CAPITAL` | $25,000 | Paper starting equity |
| `MIN_STOP_PCT` | 0.10% | Skip trade if stop < 0.10% of entry (spread noise filter) |

ATR_STOP_PCT history: paper used 5%; OOS sweep (2024–2026) showed 10% is most consistent across both periods. **Do not change without re-running the sweep.**

## File Structure

```
strategies/orb_qqq/
├── orb_qqq_config.py   — all parameters + path helpers (backtest_trades_path, etc.)
├── strategy.py         — signal logic: get_direction, generate_signal, simulate_day
├── data_fetcher.py     — Alpaca 5-min + daily bar download → Parquet cache
├── backtest.py         — walk-forward backtest engine
├── live_trader.py      — live Alpaca order management (OrbQQQLiveTrader)
└── README.md           — paper results, parameter selection rationale
```

## Running Backtests

```bash
# Paper period 2016–2023 (both instruments, both variants)
python run_orb_tqqq_backtest.py --skip-download
python run_orb_qqq_backtest.py  --skip-download

# ATR sweep: paper + OOS (generates all tagged output files)
python run_oos_sweep.py --period paper --skip-download
python run_oos_sweep.py --skip-download   # OOS 2024-01 to 2026-05
```

## Live Trading

```bash
# Start live loop (TQQQ, optimised, ATR=10%)
python orb_qqq_main.py

# Or from the Streamlit dashboard → "Start Trading" button
```

The live loop (`orb_qqq_main.py`):
- 9:35 AM: fetches opening candle + ATR14, generates signal, submits market order
- Every 60s: syncs fills → attaches stop-loss; checks stop hits
- 15:55 PM: closes all positions

State file: `data/orb_qqq_state.json` (read by dashboard for live positions display).
PID file: `data/orb_qqq_main.pid` (used by dashboard Start/Stop buttons).

## Output Files

| Path | Contents |
|---|---|
| `data/bars/orb_qqq/5min/{symbol}.parquet` | Cached 5-min bars |
| `data/bars/orb_qqq/1day/{symbol}.parquet` | Cached daily bars |
| `outputs/orbqqq_{sym}_{variant}_{tag}_trades.csv` | Backtest trade log |
| `outputs/orbqqq_{sym}_{variant}_{tag}_report.csv` | Backtest metrics |
| `outputs/orb_qqq_orders.csv` | Live order log |
| `outputs/orb_qqq_trades.csv` | Live trade log |

## ATR Sweep Tags

Output files are tagged to separate periods:
- `paper_base`, `paper_atr05` … `paper_atr35` — 2016-01 to 2023-02
- `oos_base`, `oos_atr05` … `oos_atr35` — 2024-01 to 2026-05
