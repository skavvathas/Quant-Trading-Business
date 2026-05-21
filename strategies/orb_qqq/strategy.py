"""
strategy.py — ORB QQQ/TQQQ core signal logic.

Exact implementation of Zarattini & Aziz (2023/2025):

  Baseline
  --------
  1. Observe the first 5-minute candle (9:30–9:35 ET).
  2. Skip if doji (open == close).
  3. Direction: bullish candle → LONG; bearish candle → SHORT.
  4. Entry  : open of the second 5-minute candle.
  5. Stop   : low of 1st candle (LONG) / high of 1st candle (SHORT).
  6. Target : entry ± 10 × |entry − stop|, or EOD — whichever comes first.

  Optimised (Section 4, best from Figure 7 heatmap)
  --------------------------------------------------
  Same entry, but:
  5. Stop   : entry ± 5% × 14-day ATR
  6. Target : EOD only (no fixed profit target)

Position sizing (both variants):
  Shares = int[ min( A × 0.01 / $R,  4 × A / P ) ]
  where A = account equity, $R = |P − stop|, P = entry price.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pandas as pd

from . import orb_qqq_config as cfg

MIN_STOP_PCT = 0.0010   # skip trade if stop width < 0.10% of entry price


class Direction(str, Enum):
    LONG     = "long"
    SHORT    = "short"
    NO_TRADE = "no_trade"


@dataclass
class ORBQQQSignal:
    symbol:       str
    direction:    Direction
    entry:        float     # open of 2nd 5-min candle
    stop:         float     # 1st-candle low (long) / high (short)
    target:       float     # entry ± 10R  (or entry ± large sentinel for optimised)
    risk_per_share: float   # |entry − stop|
    shares:       int
    capital:      float     # account equity at signal time
    variant:      str       # "baseline" | "optimised"


# ── Signal logic ───────────────────────────────────────────────────────────────

def get_direction(c1_open: float, c1_close: float) -> Direction:
    """
    Bullish 1st candle (close > open) → LONG.
    Bearish 1st candle (close < open) → SHORT.
    Doji (close == open)              → NO_TRADE.
    """
    if c1_close > c1_open:
        return Direction.LONG
    if c1_close < c1_open:
        return Direction.SHORT
    return Direction.NO_TRADE


def compute_stop_baseline(c1_high: float, c1_low: float, direction: Direction) -> float:
    """Stop at the opposite extreme of the first candle."""
    if direction == Direction.LONG:
        return c1_low
    return c1_high


def compute_stop_optimised(entry: float, atr14: float, direction: Direction) -> float:
    """Stop at entry ± ATR_STOP_PCT × ATR14 (paper optimum: 5%)."""
    offset = cfg.ATR_STOP_PCT * atr14
    if direction == Direction.LONG:
        return entry - offset
    return entry + offset


def compute_target(entry: float, stop: float, direction: Direction,
                   r: float = cfg.TARGET_R) -> float:
    """Profit target at entry ± r × |entry − stop|."""
    risk = abs(entry - stop)
    if direction == Direction.LONG:
        return entry + r * risk
    return entry - r * risk


def compute_shares(
    entry:     float,
    stop:      float,
    capital:   float,
    risk_pct:  float = cfg.RISK_PER_TRADE,
    max_lev:   float = cfg.MAX_LEVERAGE,
) -> int:
    """
    Paper formula: Shares = int[ min(A × 0.01 / $R,  4 × A / P) ]
    Returns 0 if risk per share is zero (degenerate).
    """
    risk_per_share = abs(entry - stop)
    if risk_per_share <= 0:
        return 0
    by_risk     = int(capital * risk_pct / risk_per_share)
    by_leverage = int(capital * max_lev / entry)
    return min(by_risk, by_leverage)


def generate_signal(
    symbol:    str,
    c1:        pd.Series,   # first 5-min bar  (index: open, high, low, close)
    c2_open:   float,       # open of second 5-min bar → entry price
    capital:   float,
    atr14:     float = 0.0, # only needed for optimised variant
    variant:   str   = "baseline",
) -> Optional[ORBQQQSignal]:
    """
    Build a signal for one trading day.
    Returns None if direction is NO_TRADE or position size is 0.
    """
    direction = get_direction(float(c1["open"]), float(c1["close"]))
    if direction == Direction.NO_TRADE:
        return None

    entry = c2_open
    if variant == "optimised" and atr14 > 0:
        stop   = compute_stop_optimised(entry, atr14, direction)
        target = 1e9 if direction == Direction.LONG else -1e9   # hold until EOD
    else:
        stop   = compute_stop_baseline(float(c1["high"]), float(c1["low"]), direction)
        target = compute_target(entry, stop, direction, r=cfg.TARGET_R)

    # Skip if stop is too tight to survive spread/slippage
    if abs(entry - stop) < MIN_STOP_PCT * entry:
        return None

    shares = compute_shares(entry, stop, capital)
    if shares == 0:
        return None

    return ORBQQQSignal(
        symbol         = symbol,
        direction      = direction,
        entry          = round(entry, 4),
        stop           = round(stop,  4),
        target         = round(target, 4),
        risk_per_share = round(abs(entry - stop), 4),
        shares         = shares,
        capital        = capital,
        variant        = variant,
    )


# ── Intraday simulator (single day) ───────────────────────────────────────────

def simulate_day(
    signal:   ORBQQQSignal,
    bars:     pd.DataFrame,  # all 5-min bars for the day, starting from candle 2
) -> dict:
    """
    Walk through intraday bars starting from the 2nd candle.
    Returns a dict with: exit_price, exit_type, pnl_per_share, bars_held.

    Exit rules:
      LONG:  exit if bar.low  ≤ stop  → fill at stop
             exit if bar.high ≥ target → fill at target
             otherwise: hold, exit at last bar close (EOD)
      SHORT: exit if bar.high ≥ stop  → fill at stop
             exit if bar.low  ≤ target → fill at target
    """
    entry     = signal.entry
    stop      = signal.stop
    target    = signal.target
    direction = signal.direction

    for i, (_, bar) in enumerate(bars.iterrows()):
        if direction == Direction.LONG:
            # Gap through stop at open
            if bar["open"] <= stop:
                exit_price = bar["open"]
                exit_type  = "stop"
                break
            if bar["low"] <= stop:
                exit_price = stop
                exit_type  = "stop"
                break
            if bar["high"] >= target:
                exit_price = target
                exit_type  = "target"
                break
        else:  # SHORT
            if bar["open"] >= stop:
                exit_price = bar["open"]
                exit_type  = "stop"
                break
            if bar["high"] >= stop:
                exit_price = stop
                exit_type  = "stop"
                break
            if bar["low"] <= target:
                exit_price = target
                exit_type  = "target"
                break
    else:
        # No exit triggered → close at EOD
        exit_price = float(bars.iloc[-1]["close"])
        exit_type  = "eod"
        i          = len(bars) - 1

    if direction == Direction.LONG:
        pnl_per_share = exit_price - entry
    else:
        pnl_per_share = entry - exit_price

    return {
        "exit_price":    round(exit_price, 4),
        "exit_type":     exit_type,
        "pnl_per_share": round(pnl_per_share, 4),
        "bars_held":     i + 1,
    }
