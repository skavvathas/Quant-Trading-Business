"""
scanner.py — Run AdaptiveTrend signal scan across the crypto universe.
Returns ranked long and short signal lists.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from . import adaptive_trend_config as cfg
from .strategy import Direction, Signal, generate_signal
from .universe import fetch_all_ohlcv, get_long_universe, get_short_universe

log = logging.getLogger(__name__)


@dataclass
class ScanResult:
    timestamp: datetime
    long_signals:  list[Signal]
    short_signals: list[Signal]
    skipped:       list[str]         # symbols with insufficient data or no signal
    bars_fetched:  int


def run_scan(
    symbols:     list[str] | None = None,
    alpha:       float = cfg.ALPHA,
    k_long:      int   = cfg.K_LONG,
    k_short:     int   = cfg.K_SHORT,
    gamma_long:  float = cfg.GAMMA_LONG,
    gamma_short: float = cfg.GAMMA_SHORT,
) -> ScanResult:
    """
    Fetch bars, generate signals, filter by Sharpe, and rank by momentum.
    Returns ScanResult with long_signals and short_signals sorted by |momentum| descending.
    """
    log.info("AdaptiveTrend scan starting — universe size %d", len(symbols or cfg.UNIVERSE))

    all_bars = fetch_all_ohlcv(symbols)
    long_universe  = get_long_universe(all_bars,  k=k_long)
    short_universe = get_short_universe(all_bars, k=k_short)

    long_signals:  list[Signal] = []
    short_signals: list[Signal] = []
    skipped:       list[str]    = []

    for sym in set(long_universe + short_universe):
        df = all_bars.get(sym)
        if df is None:
            skipped.append(sym)
            continue
        sig = generate_signal(
            symbol          = sym,
            df              = df,
            alpha           = alpha,
            gamma_long      = gamma_long,
            gamma_short     = gamma_short,
        )
        if sig is None:
            skipped.append(sym)
            continue
        if sig.direction == Direction.LONG and sym in long_universe:
            long_signals.append(sig)
        elif sig.direction == Direction.SHORT and sym in short_universe:
            short_signals.append(sig)
        else:
            skipped.append(sym)

    long_signals.sort(key=lambda s: s.momentum, reverse=True)
    short_signals.sort(key=lambda s: s.momentum)  # most negative first

    log.info(
        "Scan complete: %d long, %d short, %d skipped",
        len(long_signals), len(short_signals), len(skipped),
    )
    return ScanResult(
        timestamp     = datetime.now(tz=timezone.utc),
        long_signals  = long_signals,
        short_signals = short_signals,
        skipped       = skipped,
        bars_fetched  = len(all_bars),
    )
