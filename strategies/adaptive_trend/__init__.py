"""
AdaptiveTrend Strategy
======================

A systematic trend-following strategy for cryptocurrency perpetual swap markets,
based on the paper "Systematic Trend-Following in Crypto Markets" (2025).

Overview
--------
The strategy trades 150+ crypto perpetual pairs on 6-hour OHLCV bars.
It constructs a long-biased portfolio (70% long / 30% short) rebalanced monthly,
selecting assets by market cap and filtering by trailing Sharpe ratio.

Signal Generation
-----------------
Momentum signal over a rolling lookback window L:

    MOM_t = (P_t - P_{t-L}) / P_{t-L}

Entry rules:
  - Long  when MOM_t >  θ_entry
  - Short when MOM_t < -θ_entry_short

Trailing Stop
-------------
A long position uses a ratcheting trailing stop:

    S_t = max(S_{t-1}, P_t - α × ATR_t)

Position is closed when P_t < S_t.
Optimal α = 2.5 (ATR multiplier), balancing drawdown and upside capture.

Portfolio Construction (monthly rebalance)
------------------------------------------
  Long leg  (70% of capital):
    - Universe: top K_L = 15 coins by market cap
    - Filter:   trailing 90-day Sharpe ≥ γ_L = 1.3
    - Allocation: equal-weight within passing assets

  Short leg (30% of capital):
    - Universe: bottom K_S coins by momentum (typically the laggards)
    - Filter:   trailing 90-day Sharpe ≥ γ_S = 1.7  (higher bar)
    - Allocation: equal-weight within passing assets

Paper Performance (Jan 2022 – Dec 2024)
-----------------------------------------
  Annualised Return : +40.5%
  Sharpe Ratio      :  2.41
  Max Drawdown      : -12.7%
  Calmar Ratio      :  3.18

Transaction Cost Assumptions
-----------------------------
  Taker fee  : 4 bps per side
  Slippage   : modelled at 1 bps
  Funding rate: 8-hour perpetual funding (estimated)

Key Parameters (see adaptive_trend_config.py)
---------------------------------------------
  L           — lookback bars for momentum (default 20 × 6h bars = 5 days)
  α (ALPHA)   — ATR multiplier for trailing stop (optimal 2.5)
  λ (LAMBDA)  — fraction allocated to long leg (0.70)
  K_L         — top-N by market cap for long universe (15)
  K_S         — short-leg universe size (optional)
  γ_L         — min Sharpe for long inclusion (1.3)
  γ_S         — min Sharpe for short inclusion (1.7)
  θ_entry     — momentum entry threshold (0.02 = 2%)

Typical Universe
----------------
BTC, ETH, BNB, SOL, XRP, DOGE, ADA, AVAX, LINK, DOT, MATIC, LTC, BCH, UNI, ATOM
(and their USDT perpetual swap pairs on Binance/OKX)
"""
