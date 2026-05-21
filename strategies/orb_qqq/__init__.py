"""
ORB QQQ/TQQQ Strategy
=====================
Implementation of the 5-minute Opening Range Breakout strategy applied
exclusively to QQQ and TQQQ, as described in:

  "Can Day Trading Really Be Profitable?"
  Zarattini & Aziz, Concretum Research / Peak Capital Trading (2023/2025)

Key insight from the paper:
  A disciplined ORB trader on QQQ produces an annualised alpha of 33%
  (net of commissions, p=0.0025) that is statistically uncorrelated with
  the market (beta ≈ 0). Switching to TQQQ — a 3× leveraged ETF — bypasses
  the 4× intraday leverage constraint that caps most retail accounts, lifting
  the annualised return to 48% and total return (2016–2023) to 1,484%.

Two variants implemented:
  - Baseline : stop = 1st-candle high/low, target = 10R or EOD
  - Optimised: stop = 5% × 14-day ATR,     target = EOD only (let profits run)

Run backtest:
    PYTHONPATH=. python3 strategies/orb_qqq/backtest.py \\
        --instrument QQQ --start 2016-01-01 --end 2023-02-17 --fetch
"""
