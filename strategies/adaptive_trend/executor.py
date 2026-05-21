"""
executor.py — AdaptiveTrend order execution via Alpaca crypto trading API.
Spot crypto (USD pairs) — no leverage, long-only or long+short (paper account).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import config
from . import adaptive_trend_config as cfg
from .scanner import ScanResult
from .strategy import Direction

log = logging.getLogger(__name__)


@dataclass
class Allocation:
    symbol:    str
    direction: Direction
    notional:  float    # USD notional to trade
    weight:    float    # fraction of leg capital


def _trading_client():
    from alpaca.trading.client import TradingClient
    return TradingClient(
        api_key=config.ALPACA_API_KEY,
        secret_key=config.ALPACA_SECRET_KEY,
        paper=config.PAPER_TRADING,
    )


def allocate_capital(
    scan:         ScanResult,
    equity:       float,
    lambda_long:  float = cfg.LAMBDA_LONG,
    lambda_short: float = cfg.LAMBDA_SHORT,
) -> list[Allocation]:
    """
    Equal-weight allocation within each leg.
      Long  = equity × λ_long  / n_long
      Short = equity × λ_short / n_short
    """
    allocations: list[Allocation] = []

    n_long  = len(scan.long_signals)
    n_short = len(scan.short_signals)

    long_capital  = equity * lambda_long
    short_capital = equity * lambda_short

    for sig in scan.long_signals:
        w = 1.0 / n_long if n_long else 0.0
        allocations.append(Allocation(
            symbol    = sig.symbol,
            direction = Direction.LONG,
            notional  = long_capital * w,
            weight    = w,
        ))

    for sig in scan.short_signals:
        w = 1.0 / n_short if n_short else 0.0
        allocations.append(Allocation(
            symbol    = sig.symbol,
            direction = Direction.SHORT,
            notional  = short_capital * w,
            weight    = w,
        ))

    return allocations


def submit_orders(allocations: list[Allocation]) -> list[dict]:
    """
    Submit notional market orders via Alpaca crypto API.
    Alpaca supports fractional crypto orders by notional USD amount.
    Returns list of order result dicts.
    """
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    client = _trading_client()
    results = []

    for alloc in allocations:
        if alloc.notional < 1.0:
            log.warning("Skipping %s — notional $%.2f below $1 minimum", alloc.symbol, alloc.notional)
            continue

        side = OrderSide.BUY if alloc.direction == Direction.LONG else OrderSide.SELL

        # Alpaca crypto: use notional for fractional sizing
        req = MarketOrderRequest(
            symbol       = alloc.symbol,
            notional     = round(alloc.notional, 2),
            side         = side,
            time_in_force= TimeInForce.IOC,
        )

        try:
            order = client.submit_order(req)
            log.info(
                "Order submitted: %s %s $%.2f → id=%s",
                side.value, alloc.symbol, alloc.notional, order.id,
            )
            results.append({
                "symbol":    alloc.symbol,
                "side":      side.value,
                "notional":  alloc.notional,
                "order_id":  str(order.id),
                "status":    str(order.status),
            })
        except Exception as e:
            log.error("Order failed for %s: %s", alloc.symbol, e)
            results.append({
                "symbol":   alloc.symbol,
                "side":     side.value,
                "notional": alloc.notional,
                "order_id": None,
                "status":   f"ERROR: {e}",
            })

    return results


def get_open_positions() -> dict[str, dict]:
    """Return {symbol: {side, qty, market_value, unrealized_pl}} for open crypto positions."""
    client = _trading_client()
    positions = {}
    try:
        for pos in client.get_all_positions():
            if "/" in pos.symbol or pos.asset_class == "crypto":
                positions[pos.symbol] = {
                    "side":          pos.side.value,
                    "qty":           float(pos.qty),
                    "market_value":  float(pos.market_value),
                    "unrealized_pl": float(pos.unrealized_pl),
                    "avg_entry":     float(pos.avg_entry_price),
                    "current_price": float(pos.current_price),
                }
    except Exception as e:
        log.error("get_open_positions: %s", e)
    return positions


def close_position(symbol: str) -> bool:
    """Close entire position for a symbol (trailing stop triggered)."""
    client = _trading_client()
    try:
        client.close_position(symbol)
        log.info("Closed position: %s", symbol)
        return True
    except Exception as e:
        log.error("close_position(%s): %s", symbol, e)
        return False
