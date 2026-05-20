"""
Opening Range Breakout (ORB) Strategy
Based on: "A Profitable Day Trading Strategy For The U.S. Equity Market"
Zarattini, Barbon, Aziz (SFI Research Paper N°24-98)
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pandas as pd

from strategies.orb import orb_config


class Direction(Enum):
    LONG = "long"
    SHORT = "short"
    NO_TRADE = "no_trade"


@dataclass
class ORBSignal:
    ticker: str
    direction: Direction
    entry_price: float       # stop order trigger level (5-min high or low)
    stop_loss: float         # dynamic ATR-tier stop
    take_profit: float       # R-multiple limit order target
    atr: float
    relative_volume: float
    opening_range_high: float
    opening_range_low: float


@dataclass
class ORBSetup:
    """All pre-computed inputs needed to evaluate a stock for ORB on a given day."""
    ticker: str
    open_price: float
    first_candle_open: float
    first_candle_close: float
    first_candle_high: float
    first_candle_low: float
    first_candle_volume: float
    avg_14d_volume: float        # average full-day volume over last 14 days
    avg_14d_orvolume: float      # average 5-min opening range volume over last 14 days
    atr_14d: float               # 14-day ATR (daily)


# --- Filters ---

def passes_base_filters(setup: ORBSetup) -> bool:
    """
    Paper Section 2.1 base filters:
      1. Opening price > $5
      2. 14-day average daily volume >= 1,000,000 shares
      3. 14-day ATR > $0.50
    """
    return (
        setup.open_price > 5.0
        and setup.avg_14d_volume >= 1_000_000
        and setup.atr_14d > 0.50
    )


def compute_relative_volume(setup: ORBSetup) -> float:
    """
    RelVol = today's 5-min opening range volume / 14-day average 5-min opening range volume.
    Returns 0 if historical average is zero (avoids division by zero).
    """
    if setup.avg_14d_orvolume == 0:
        return 0.0
    return setup.first_candle_volume / setup.avg_14d_orvolume


def passes_relvol_filter(relative_volume: float, min_relvol: float = 1.0) -> bool:
    """Relative Volume must be >= min_relvol (1.0 = 100%)."""
    return relative_volume >= min_relvol


# --- Opening range direction ---

def get_direction(setup: ORBSetup) -> Direction:
    """
    Bullish first candle (close > open)  → LONG only.
    Bearish first candle (close < open)  → SHORT only.
    Doji (close == open)                 → NO_TRADE.
    """
    if setup.first_candle_close > setup.first_candle_open:
        return Direction.LONG
    elif setup.first_candle_close < setup.first_candle_open:
        return Direction.SHORT
    else:
        return Direction.NO_TRADE


# --- Entry and stop loss ---

def compute_entry_price(setup: ORBSetup, direction: Direction) -> Optional[float]:
    """
    Entry is a stop order placed at the opening range boundary in the trade direction.
    LONG  → stop buy  at the 5-min HIGH
    SHORT → stop sell at the 5-min LOW
    """
    if direction == Direction.LONG:
        return setup.first_candle_high
    elif direction == Direction.SHORT:
        return setup.first_candle_low
    return None


def _stop_distance(atr: float) -> float:
    """Dynamic stop distance from ATR tiers defined in orb_config.ATR_TIERS."""
    for tier in orb_config.ATR_TIERS:
        if atr >= tier["atr_min"]:
            return tier["stop_value"] if tier["stop_is_fixed"] else tier["stop_value"] * atr
    return atr  # fallback: 1× ATR


def _tp_r(atr: float) -> float:
    """R-multiple for take-profit from ATR tiers."""
    for tier in orb_config.ATR_TIERS:
        if atr >= tier["atr_min"]:
            return tier["tp_r"]
    return 2.0


def compute_stop_loss(entry_price: float, atr: float, direction: Direction,
                      atr_pct: float = None) -> float:
    """
    Dynamic stop loss based on ATR tier (see orb_config.ATR_TIERS).
    Pass atr_pct explicitly only for legacy/backtest use.
    """
    offset = (atr_pct * atr) if atr_pct is not None else _stop_distance(atr)
    if direction == Direction.LONG:
        return entry_price - offset
    else:
        return entry_price + offset


def compute_take_profit(entry_price: float, stop_loss: float,
                        atr: float, direction: Direction) -> float:
    """Take-profit = entry ± (stop_distance × R-multiple)."""
    risk = abs(entry_price - stop_loss)
    offset = risk * _tp_r(atr)
    if direction == Direction.LONG:
        return entry_price + offset
    else:
        return entry_price - offset


# --- Position sizing ---

def compute_shares(
    entry_price: float,
    stop_loss: float,
    capital: float,
    risk_pct: float = 0.01,
    max_leverage: float = 4.0,
) -> int:
    """
    Size each position so that a stop-loss hit costs exactly risk_pct of capital.
    Capped at max_leverage * capital / entry_price shares (4x leverage limit).

    Returns 0 if risk per share is zero or negative (degenerate input).
    """
    risk_per_share = abs(entry_price - stop_loss)
    if risk_per_share <= 0:
        return 0

    shares_by_risk = int((capital * risk_pct) / risk_per_share)
    shares_by_leverage = int((capital * max_leverage) / entry_price)
    return min(shares_by_risk, shares_by_leverage)


# --- Main signal generator ---

def generate_signal(setup: ORBSetup, min_relvol: float = 1.0) -> Optional[ORBSignal]:
    """
    Full pipeline for a single stock on a single day.
    Returns an ORBSignal if all conditions are met, otherwise None.
    """
    if not passes_base_filters(setup):
        return None

    relative_volume = compute_relative_volume(setup)
    if not passes_relvol_filter(relative_volume, min_relvol):
        return None

    direction = get_direction(setup)
    if direction == Direction.NO_TRADE:
        return None

    entry_price = compute_entry_price(setup, direction)
    stop_loss   = compute_stop_loss(entry_price, setup.atr_14d, direction)
    take_profit = compute_take_profit(entry_price, stop_loss, setup.atr_14d, direction)

    return ORBSignal(
        ticker=setup.ticker,
        direction=direction,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        atr=setup.atr_14d,
        relative_volume=relative_volume,
        opening_range_high=setup.first_candle_high,
        opening_range_low=setup.first_candle_low,
    )


def select_top_n(signals: list[ORBSignal], n: int = 20) -> list[ORBSignal]:
    """
    From a list of valid signals, keep only the top N by Relative Volume.
    This implements the paper's 'top 20 Stocks in Play' filter.
    """
    return sorted(signals, key=lambda s: s.relative_volume, reverse=True)[:n]
