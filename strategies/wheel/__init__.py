"""
Wheel Strategy
==============

The Wheel is a systematic options income strategy that cycles through two legs:

  1. Cash-Secured Put (CSP)
     Sell a put option on a stock you're willing to own.
     You collect premium upfront. Two outcomes:
       - Put expires worthless  → keep premium, repeat.
       - Put is assigned (stock falls below strike) → you buy shares at the strike
         price, but your effective cost basis is (strike − premium collected).

  2. Covered Call (CC)  [entered only after assignment]
     Sell a call option against the shares you now own.
     You collect premium again. Two outcomes:
       - Call expires worthless → keep premium, sell another CC.
       - Call is assigned (stock rises above strike) → shares are sold at the
         strike price, capturing any appreciation + premium. Wheel resets.

Cycle summary:
  Sell CSP → [not assigned] → repeat CSP
           → [assigned]     → own shares → sell CC → [not called] → repeat CC
                                                    → [called away] → reset

Key parameters (to be codified in wheel_config.py):
  - Target delta for CSP / CC:  0.20–0.35 (controls risk vs premium)
  - Days to expiration (DTE):   30–45 DTE at entry for optimal theta decay
  - Strike selection:           CSP below current price; CC above cost basis
  - Position sizing:            Cash required = strike × 100 per contract
  - Min premium threshold:      e.g. annualised yield > 20%
  - Stock selection:            High IV rank, liquid options, stocks you'd hold

Risk factors:
  - Assignment risk:  stock drops sharply below CSP strike (paper loss on shares).
  - Cap on upside:    CC limits gains if stock rallies strongly after assignment.
  - Earnings risk:    avoid holding through earnings — implied vol collapses after.
  - Liquidity:        wide bid/ask spreads erode edge; stick to highly liquid names.

Typical candidates: AAPL, TSLA, NVDA, AMD, SPY, QQQ — high option volume,
                    tight spreads, strong underlying businesses.
"""
