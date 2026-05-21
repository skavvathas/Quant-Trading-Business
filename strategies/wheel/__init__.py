"""
Wheel Strategy
==============
Systematic options income strategy that alternates between two legs:

  1. Cash-Secured Put (CSP) — sell a put at ~25-delta, 35 DTE.
       • Expires OTM  → keep premium, repeat.
       • Assigned     → own 100 shares at cost basis = strike − premium.

  2. Covered Call (CC) — sell a call at ~25-delta above cost basis.
       • Expires OTM  → keep premium, sell another CC.
       • Called away  → shares sold at CC strike, cycle resets.

Entry filter: IV Rank ≥ 30 and annualised yield ≥ 20%.
Early close:  buy back at 50% profit (theta-decay sweet spot).
Universe:     AAPL, MSFT, NVDA, AMD, TSLA, SPY, QQQ and others.
Data:         yfinance daily OHLCV + Black-Scholes (HV30 × 1.15 as IV proxy).
Execution:    Alpaca options API (options-approved account required).
"""
