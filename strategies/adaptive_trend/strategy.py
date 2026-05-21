"""
strategy.py — AdaptiveTrend core signal and trailing stop logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

from . import adaptive_trend_config as cfg


class Direction(str, Enum):
    LONG  = "long"
    SHORT = "short"
    FLAT  = "flat"


@dataclass
class Signal:
    symbol:    str
    direction: Direction
    momentum:  float          # raw MOM value
    atr:       float          # current ATR
    price:     float          # close at signal time
    trailing_stop: float      # initial trailing-stop level
    sharpe:    float = 0.0    # trailing Sharpe (used for position filter)


@dataclass
class Position:
    symbol:        str
    direction:     Direction
    entry_price:   float
    trailing_stop: float
    shares:        float = 0.0
    pnl:           float = 0.0
    bars_held:     int   = 0


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = cfg.ATR_PERIOD) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def compute_momentum(close: pd.Series, lookback: int = cfg.LOOKBACK_BARS) -> pd.Series:
    """MOM_t = (P_t - P_{t-L}) / P_{t-L}"""
    return (close - close.shift(lookback)) / close.shift(lookback)


def compute_trailing_stop(
    price: float,
    prev_stop: Optional[float],
    atr: float,
    direction: Direction,
    alpha: float = cfg.ALPHA,
) -> float:
    """Ratchet trailing stop. For longs: S_t = max(S_{t-1}, P_t - α×ATR)."""
    new_stop = price - alpha * atr if direction == Direction.LONG else price + alpha * atr
    if prev_stop is None:
        return new_stop
    if direction == Direction.LONG:
        return max(prev_stop, new_stop)
    return min(prev_stop, new_stop)


def compute_trailing_sharpe(returns: pd.Series, lookback: int = cfg.SHARPE_LOOKBACK_BARS) -> float:
    """Annualised Sharpe over the trailing lookback window (6h bars → 4 bars/day)."""
    window = returns.iloc[-lookback:].dropna()
    if len(window) < 30:
        return 0.0
    bars_per_year = 4 * 365  # 6h bars
    mean = window.mean()
    std  = window.std(ddof=1)
    if std == 0:
        return 0.0
    return float(mean / std * np.sqrt(bars_per_year))


def generate_signal(
    symbol: str,
    df: pd.DataFrame,
    alpha: float = cfg.ALPHA,
    entry_threshold: float = cfg.ENTRY_THRESHOLD,
    short_threshold: float = cfg.SHORT_THRESHOLD,
    gamma_long: float = cfg.GAMMA_LONG,
    gamma_short: float = cfg.GAMMA_SHORT,
) -> Optional[Signal]:
    """
    Compute MOM + trailing stop + Sharpe for the latest bar.
    Returns a Signal or None if no entry condition is met.

    df must have columns: open, high, low, close, volume (lowercase).
    Index should be a DatetimeIndex sorted ascending.
    """
    if len(df) < cfg.LOOKBACK_BARS + cfg.ATR_PERIOD + 5:
        return None

    close = df["close"]
    high  = df["high"]
    low   = df["low"]

    atr_series = compute_atr(high, low, close)
    mom_series = compute_momentum(close)

    latest_mom   = mom_series.iloc[-1]
    latest_atr   = atr_series.iloc[-1]
    latest_price = close.iloc[-1]

    if pd.isna(latest_mom) or pd.isna(latest_atr):
        return None

    returns = close.pct_change()
    sharpe  = compute_trailing_sharpe(returns)

    if latest_mom > entry_threshold and sharpe >= gamma_long:
        direction    = Direction.LONG
        trail_stop   = latest_price - alpha * latest_atr
    elif latest_mom < -short_threshold and sharpe >= gamma_short:
        direction    = Direction.SHORT
        trail_stop   = latest_price + alpha * latest_atr
    else:
        return None

    return Signal(
        symbol        = symbol,
        direction     = direction,
        momentum      = round(latest_mom,   6),
        atr           = round(latest_atr,   6),
        price         = round(latest_price, 6),
        trailing_stop = round(trail_stop,   6),
        sharpe        = round(sharpe,       4),
    )


def update_position(pos: Position, current_price: float, current_atr: float) -> Position:
    """Update trailing stop and PnL for an open position. Returns updated position."""
    pos.trailing_stop = compute_trailing_stop(
        current_price, pos.trailing_stop, current_atr, pos.direction
    )
    if pos.direction == Direction.LONG:
        pos.pnl = (current_price - pos.entry_price) * pos.shares
    else:
        pos.pnl = (pos.entry_price - current_price) * pos.shares
    pos.bars_held += 1
    return pos


def should_close(pos: Position, current_price: float) -> bool:
    """True if trailing stop is triggered."""
    if pos.direction == Direction.LONG:
        return current_price <= pos.trailing_stop
    return current_price >= pos.trailing_stop
