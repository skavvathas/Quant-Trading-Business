"""
strategy.py — Wheel options core: Black-Scholes pricing, delta, strike selection.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from . import wheel_config as cfg

RISK_FREE_RATE = 0.05  # approximate risk-free rate (update as needed)


class LegType(str, Enum):
    CSP = "csp"
    CC  = "cc"


class ExitType(str, Enum):
    PROFIT_CLOSE = "profit_close"    # bought back at 50%
    EXPIRED_OTM  = "expired_otm"     # expired worthless
    ASSIGNED     = "assigned"        # CSP: stock below strike at expiry
    CALLED_AWAY  = "called_away"     # CC: stock above strike at expiry
    FORCE_CLOSE  = "force_close"     # end-of-backtest liquidation


@dataclass
class OptionSignal:
    symbol:      str
    leg_type:    LegType
    stock_price: float
    strike:      float
    iv:          float      # annualized implied vol
    iv_rank:     float      # 0–100
    dte:         int
    premium:     float      # per-share premium (net of slippage)
    delta:       float      # abs delta
    ann_yield:   float      # annualized premium yield on capital secured


@dataclass
class WheelPosition:
    symbol:           str
    leg_type:         LegType
    strike:           float
    entry_price:      float      # stock price when leg was opened
    premium_in:       float      # per-share premium received at entry
    iv:               float      # IV used to price this leg
    entry_dt:         object     # pd.Timestamp
    expiry_dt:        object     # pd.Timestamp
    csp_strike:       float      # original CSP strike (for CC cost-basis tracking)
    csp_premium:      float      # original CSP premium (for CC cost-basis tracking)
    contracts:        int = field(default=1)


# ── Black-Scholes ──────────────────────────────────────────────────────────────

def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(
    S: float, K: float, T: float, r: float, sigma: float, option_type: str = "put"
) -> float:
    """Black-Scholes option price per share."""
    if T <= 1e-6 or sigma <= 1e-6:
        if option_type == "put":
            return max(K - S, 0.0)
        return max(S - K, 0.0)

    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if option_type == "put":
        return max(K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1), 0.0)
    return max(S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2), 0.0)


def bs_delta(
    S: float, K: float, T: float, r: float, sigma: float, option_type: str = "put"
) -> float:
    """Black-Scholes delta. Returns negative for puts, positive for calls."""
    if T <= 1e-6 or sigma <= 1e-6:
        return -1.0 if option_type == "put" else 1.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    if option_type == "put":
        return _ncdf(d1) - 1.0
    return _ncdf(d1)


def find_strike_by_delta(
    S: float,
    T: float,
    r: float,
    sigma: float,
    target_delta: float,
    option_type: str = "put",
) -> float:
    """Binary search for strike that yields abs(delta) ≈ target_delta."""
    if option_type == "put":
        # Puts: delta ranges from ~0 (deep OTM, K<<S) to ~-1 (deep ITM, K>>S)
        # target is negative (e.g. -0.25); larger K → more negative delta
        lo, hi = S * 0.50, S * 0.999
        target = -abs(target_delta)
        for _ in range(60):
            mid = (lo + hi) / 2.0
            d = bs_delta(S, mid, T, r, sigma, "put")
            if d > target:    # delta not negative enough → need larger K
                lo = mid
            else:             # delta too negative → need smaller K
                hi = mid
            if hi - lo < 0.01:
                break
    else:
        # Calls: delta ranges from ~1 (deep ITM, K<<S) to ~0 (deep OTM, K>>S)
        # target is positive (e.g. 0.25); larger K → smaller delta
        lo, hi = S * 1.001, S * 1.60
        target = abs(target_delta)
        for _ in range(60):
            mid = (lo + hi) / 2.0
            d = bs_delta(S, mid, T, r, sigma, "call")
            if d > target:    # delta too large → need larger K
                lo = mid
            else:             # delta not large enough → need smaller K
                hi = mid
            if hi - lo < 0.01:
                break

    raw = (lo + hi) / 2.0
    # Round to nearest $0.50 (standard strike increment for most equities)
    return round(raw * 2) / 2


def annualized_yield(premium: float, capital: float, dte: int) -> float:
    """(premium / capital) × (365 / dte)."""
    if capital <= 0 or dte <= 0:
        return 0.0
    return (premium / capital) * (365.0 / dte)


def commission_cost(legs: int = 1) -> float:
    """Total commission for n option legs."""
    return cfg.COMMISSION_PER_CONTRACT * legs
