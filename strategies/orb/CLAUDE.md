# ORB Strategy (Multi-Stock)

Opening Range Breakout applied to a broad universe of liquid US equities, based on:
> *"A Profitable Day Trading Strategy For The U.S. Equity Market"* — Zarattini, Barbon & Aziz (SFI Research Paper N°24-98)

This is the **general-stock** ORB strategy (not the QQQ/TQQQ-specific one). It scans ~4,000 stocks overnight, selects the top 20 "stocks in play" by relative volume, and trades their 5-min opening ranges.

## Strategy Logic

1. **Overnight scan** (8:00 AM) — filter universe: price > $5, avg volume > 1M, ATR > $0.50
2. **9:35 AM scan** — rank survivors by relative opening-range volume (RelVol); keep top 20
3. **Entry** — stop order at the 5-min OR high (LONG) or low (SHORT)
4. **Stop loss** — ATR-tier system (see below); tighter on high-ATR stocks
5. **Take profit** — R-multiple limit order (3–4R depending on ATR tier)
6. **EOD** — cancel unfilled entries, close all positions at 15:50 ET

### ATR-Tier Stop System (`orb_config.py`)

| ATR Range | Stop | TP |
|---|---|---|
| ATR ≥ $10 | $0.75 fixed | 4R |
| ATR ≥ $5  | 0.50 × ATR | 4R |
| ATR ≥ $2  | 0.75 × ATR | 3.5R |
| ATR < $2  | 1.00 × ATR | 3R |

## Key Parameters (`orb_config.py`)

| Parameter | Value | Meaning |
|---|---|---|
| `MIN_PRICE` | $5 | Min opening price |
| `MIN_AVG_VOLUME` | 1,000,000 | 14-day avg daily volume |
| `MIN_ATR` | $0.50 | 14-day ATR floor |
| `MIN_RELVOL` | 1.0 | RelVol ≥ 100% to qualify as "in play" |
| `TOP_N` | 20 | Max signals per day |
| `RISK_PER_TRADE` | 1.5% | Account risk per trade if stop hit |
| `MAX_LEVERAGE` | 4× | FINRA intraday cap |
| `MAX_DAILY_LOSS_PCT` | 2.5% | Kill switch: stop trading if down 2.5% |
| `MAX_DAILY_GAIN_PCT` | 5.0% | Stop trading if up 5% (lock in gains) |

## File Structure

```
strategies/orb/
├── orb_config.py    — all parameters + output paths
├── strategy.py      — ORBSignal, ORBSetup, filters, compute_shares, signal logic
├── scanner.py       — run_scan(): fetch 5-min bars, compute RelVol, rank signals
├── universe.py      — build_watchlist() / load_watchlist() (4,000-stock overnight filter)
├── data_fetcher.py  — Alpaca bar fetcher (5-min + daily, batched)
├── backtest.py      — walk-forward backtest
└── executor.py      — ORBExecutor: order lifecycle (stop entry → SL → TP → EOD close)
```

## Running

```bash
# Live loop (started from app root)
python orb_main.py

# Backtest
python strategies/orb/backtest.py
```

The live loop (`orb_main.py` at project root):
- 8:00 AM: rebuild watchlist (if stale)
- 9:35 AM: run_scan() → submit_entry_orders()
- Every 60s: sync_fills() + sync_stop_loss_hits() + sync_take_profit_hits()
- 15:50 PM: close_all_positions() + cancel_pending_entries()

State file: `data/orb_state.json` (read by the ORB dashboard).

## Output Files

| Path | Contents |
|---|---|
| `data/orb_watchlist.json` | Overnight filtered universe |
| `outputs/orb_orders.csv` | Live order log |
| `outputs/orb_trades.csv` | Live trade log |
| `logs/trades/trades_YYYY-MM-DD.csv` | Per-day trade log |

## Key Differences vs. orb_qqq

| | orb (this) | orb_qqq |
|---|---|---|
| Universe | ~4,000 stocks, top 20 by RelVol | QQQ + TQQQ fixed |
| Entry | Stop order at OR high/low | Market order at 9:35 open |
| Stop | ATR-tier table | Fixed % of ATR (10%) |
| Target | 3–4R limit order | EOD (optimised) or 10R (baseline) |
| Scan time | Pre-market + 9:35 | 9:35 only |
