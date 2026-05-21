"""
scanner.py — Scan equity universe for Wheel CSP entry candidates.
Ranks by annualised premium yield after IV-rank and minimum-yield filters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from . import wheel_config as cfg
from .strategy import (
    LegType, OptionSignal, RISK_FREE_RATE,
    annualized_yield, bs_delta, bs_price, find_strike_by_delta,
)
from .universe import fetch_all_ohlcv, get_stock_metrics

log = logging.getLogger(__name__)


@dataclass
class ScanResult:
    timestamp: datetime
    signals:   list[OptionSignal]
    skipped:   list[str]
    n_symbols: int


def _score_csp(
    symbol: str,
    price:  float,
    iv:     float,
    iv_rank: float,
    dte:    int   = cfg.TARGET_DTE,
) -> Optional[OptionSignal]:
    T      = dte / 365.0
    strike = find_strike_by_delta(price, T, RISK_FREE_RATE, iv, cfg.CSP_TARGET_DELTA, "put")
    raw    = bs_price(price, strike, T, RISK_FREE_RATE, iv, "put")
    # Net of bid/ask slippage (we receive a bit less than mid)
    premium = raw * (1.0 - cfg.SLIPPAGE_BPS / 10_000)
    delta   = abs(bs_delta(price, strike, T, RISK_FREE_RATE, iv, "put"))
    ann_y   = annualized_yield(premium, strike, dte)

    if ann_y < cfg.MIN_ANN_YIELD:
        return None

    return OptionSignal(
        symbol      = symbol,
        leg_type    = LegType.CSP,
        stock_price = round(price,   2),
        strike      = round(strike,  2),
        iv          = round(iv,      4),
        iv_rank     = round(iv_rank, 1),
        dte         = dte,
        premium     = round(premium, 4),
        delta       = round(delta,   4),
        ann_yield   = round(ann_y,   4),
    )


def run_scan(symbols: list[str] | None = None) -> ScanResult:
    """
    Fetch live bars, compute IV-rank and BS pricing, filter, and rank by yield.
    Returns ScanResult sorted by annualised yield descending.
    """
    log.info("Wheel scan starting — universe %d", len(symbols or cfg.UNIVERSE))
    bars    = fetch_all_ohlcv(symbols)
    metrics = get_stock_metrics(bars)

    signals: list[OptionSignal] = []
    skipped: list[str] = []

    for sym, m in metrics.items():
        if m["iv_rank"] < cfg.MIN_IV_RANK or m["implied_vol"] <= 0:
            skipped.append(sym)
            continue

        sig = _score_csp(
            symbol  = sym,
            price   = m["price"],
            iv      = m["implied_vol"],
            iv_rank = m["iv_rank"],
        )
        if sig is None:
            skipped.append(sym)
            continue
        signals.append(sig)

    signals.sort(key=lambda s: s.ann_yield, reverse=True)
    log.info("Scan complete: %d signals, %d skipped", len(signals), len(skipped))

    return ScanResult(
        timestamp = datetime.now(tz=timezone.utc),
        signals   = signals,
        skipped   = skipped,
        n_symbols = len(metrics),
    )
