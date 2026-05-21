# Wheel Strategy

Options income strategy: sell Cash-Secured Puts (CSP), take assignment, then sell Covered Calls (CC) until called away — repeating the cycle indefinitely.

## Strategy Logic

1. **Scan** — filter universe by IV rank ≥ 30 and annualised premium yield ≥ 20%
2. **CSP leg** — sell a put at ~25-delta, ~35 DTE
3. **Close or assign** — buy back at 50% profit, or take stock assignment if put expires ITM
4. **CC leg** — sell a call at ~25-delta above cost basis
5. **Called away or repeat** — if stock closes above strike at expiry, sell shares; otherwise sell another CC

## Key Parameters (`wheel_config.py`)

| Parameter | Value | Meaning |
|---|---|---|
| `CSP_TARGET_DELTA` | 0.25 | ~25-delta put (5–10% OTM) |
| `CC_TARGET_DELTA` | 0.25 | ~25-delta call |
| `TARGET_DTE` | 35 | Days to expiry at entry |
| `CLOSE_PROFIT_PCT` | 0.50 | Close at 50% of premium received |
| `MIN_IV_RANK` | 30 | Only sell when IV is elevated |
| `MIN_ANN_YIELD` | 0.20 | Min 20% annualised yield on capital secured |
| `MAX_POSITION_PCT` | 0.10 | Max 10% of equity per position |
| `MAX_POSITIONS` | 5 | Max concurrent wheel positions |
| `COMMISSION_PER_CONTRACT` | $0.65 | Per contract leg |

## Universe

16 liquid large-caps: AAPL, MSFT, NVDA, AMD, TSLA, AMZN, META, GOOGL, SPY, QQQ, JPM, BAC, GS, MS, XOM, CVX.

## File Structure

```
strategies/wheel/
├── wheel_config.py   — all parameters + output paths
├── strategy.py       — Black-Scholes pricing, delta, strike selection, OptionSignal
├── scanner.py        — screens universe for CSP/CC candidates
├── universe.py       — loads/filters the equity universe
├── backtest.py       — walk-forward simulation
├── executor.py       — Alpaca options order management
└── __init__.py
```

## Running

```bash
# Backtest
python -m strategies.wheel.backtest

# Scan for live signals
python -m strategies.wheel.scanner
```

## Output Files

| Path | Contents |
|---|---|
| `data/wheel_scan.json` | Latest scan results |
| `data/bars/wheel_equity/` | Cached daily bars (yfinance) |
| `outputs/wheel_backtest_trades.csv` | Per-trade log |
| `outputs/wheel_backtest_report.csv` | Summary metrics |

## Pricing Model

Uses Black-Scholes for premium and delta. IV proxy = `HV30 × 1.15` (backtest only; live uses Alpaca options chain). Slippage assumed at 10 bps per side.
