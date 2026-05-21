"""
executor.py — Wheel strategy order execution via Alpaca options API.
Requires an options-approved paper or live account.
OCC symbol format: AAPL240621P00190000
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import config
from . import wheel_config as cfg
from .strategy import LegType, OptionSignal, RISK_FREE_RATE

log = logging.getLogger(__name__)


def _trading_client():
    from alpaca.trading.client import TradingClient
    return TradingClient(
        api_key=config.ALPACA_API_KEY,
        secret_key=config.ALPACA_SECRET_KEY,
        paper=config.PAPER_TRADING,
    )


def _to_occ(symbol: str, expiry: date, strike: float, option_type: str) -> str:
    """
    Build OCC option symbol.
    Format: <SYMBOL(6)><YYMMDD><C|P><STRIKE(8, strike×1000 zero-padded)>
    Example: AAPL240621P00190000 → AAPL, Jun 21 2024, Put, $190.00
    """
    sym    = symbol.upper().ljust(6)[:6]
    exp    = expiry.strftime("%y%m%d")
    cp     = "C" if option_type.lower() == "call" else "P"
    s_int  = int(round(strike * 1000))
    return f"{sym}{exp}{cp}{s_int:08d}"


def _next_expiry(dte: int = cfg.TARGET_DTE) -> date:
    """Return the nearest Friday at or after today + dte days."""
    target = date.today() + timedelta(days=dte)
    while target.weekday() != 4:   # 4 = Friday
        target += timedelta(days=1)
    return target


@dataclass
class OrderResult:
    symbol:     str
    occ_symbol: str
    leg_type:   LegType
    strike:     float
    expiry:     date
    qty:        int
    order_id:   Optional[str]
    status:     str


def submit_csp(signal: OptionSignal, contracts: int = cfg.CONTRACTS) -> OrderResult:
    """Sell to open a cash-secured put."""
    from alpaca.trading.requests import OptionMarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    expiry = _next_expiry(signal.dte)
    occ    = _to_occ(signal.symbol, expiry, signal.strike, "put")

    try:
        client = _trading_client()
        req = OptionMarketOrderRequest(
            symbol        = occ,
            qty           = contracts,
            side          = OrderSide.SELL,
            time_in_force = TimeInForce.DAY,
        )
        order = client.submit_order(req)
        log.info("CSP submitted: %s ×%d id=%s", occ, contracts, order.id)
        return OrderResult(
            symbol=signal.symbol, occ_symbol=occ, leg_type=LegType.CSP,
            strike=signal.strike, expiry=expiry, qty=contracts,
            order_id=str(order.id), status=str(order.status),
        )
    except Exception as e:
        log.error("submit_csp(%s): %s", occ, e)
        return OrderResult(
            symbol=signal.symbol, occ_symbol=occ, leg_type=LegType.CSP,
            strike=signal.strike, expiry=expiry, qty=contracts,
            order_id=None, status=f"ERROR: {e}",
        )


def submit_cc(
    symbol:    str,
    strike:    float,
    contracts: int = cfg.CONTRACTS,
    dte:       int = cfg.TARGET_DTE,
) -> OrderResult:
    """Sell to open a covered call. Called after CSP assignment."""
    from alpaca.trading.requests import OptionMarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    expiry = _next_expiry(dte)
    occ    = _to_occ(symbol, expiry, strike, "call")

    try:
        client = _trading_client()
        req = OptionMarketOrderRequest(
            symbol        = occ,
            qty           = contracts,
            side          = OrderSide.SELL,
            time_in_force = TimeInForce.DAY,
        )
        order = client.submit_order(req)
        log.info("CC submitted: %s ×%d id=%s", occ, contracts, order.id)
        return OrderResult(
            symbol=symbol, occ_symbol=occ, leg_type=LegType.CC,
            strike=strike, expiry=expiry, qty=contracts,
            order_id=str(order.id), status=str(order.status),
        )
    except Exception as e:
        log.error("submit_cc(%s): %s", occ, e)
        return OrderResult(
            symbol=symbol, occ_symbol=occ, leg_type=LegType.CC,
            strike=strike, expiry=expiry, qty=contracts,
            order_id=None, status=f"ERROR: {e}",
        )


def buy_to_close(occ_symbol: str, contracts: int = cfg.CONTRACTS) -> bool:
    """Buy to close an open short option (take 50% profit)."""
    from alpaca.trading.requests import OptionMarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    try:
        client = _trading_client()
        req = OptionMarketOrderRequest(
            symbol        = occ_symbol,
            qty           = contracts,
            side          = OrderSide.BUY,
            time_in_force = TimeInForce.DAY,
        )
        order = client.submit_order(req)
        log.info("BTC submitted: %s ×%d id=%s", occ_symbol, contracts, order.id)
        return True
    except Exception as e:
        log.error("buy_to_close(%s): %s", occ_symbol, e)
        return False


def get_open_option_positions() -> dict[str, dict]:
    """Return open option positions keyed by OCC symbol."""
    client = _trading_client()
    positions = {}
    try:
        for pos in client.get_all_positions():
            if pos.asset_class and "option" in str(pos.asset_class).lower():
                positions[pos.symbol] = {
                    "qty":           float(pos.qty),
                    "market_value":  float(pos.market_value),
                    "unrealized_pl": float(pos.unrealized_pl),
                    "avg_entry":     float(pos.avg_entry_price),
                    "current_price": float(pos.current_price),
                    "side":          pos.side.value,
                }
    except Exception as e:
        log.error("get_open_option_positions: %s", e)
    return positions
