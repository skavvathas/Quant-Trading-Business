"""
executor.py — Order submission, fill tracking, and exit management.

OrderManager  : wraps Alpaca order API + CSV logging
ExitManager   : take-profit, stop-loss, and time-based exits
"""

import csv
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytz

import config

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")


# ── CSV helpers ────────────────────────────────────────────────────────────────

def _write_csv_row(path: Path, row: dict) -> None:
    """Append a row dict to a CSV file, writing headers on first write."""
    write_header = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=row.keys())
        if write_header:
            writer.writeheader()
        writer.writerow(row)


# ── OrderManager ───────────────────────────────────────────────────────────────

class OrderManager:
    """
    Submit orders, track open positions, and log to orders.csv.

    open_positions: { symbol: { entry_price, qty, side, order_id,
                                entry_time, unrealized_pnl } }
    """

    def __init__(self):
        self.open_positions: dict = {}
        self._pending_orders: dict = {}   # order_id → order dict

    # ── Alpaca client ──────────────────────────────────────────────────────────

    def _trading_client(self):
        from alpaca.trading.client import TradingClient
        return TradingClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
            paper=config.PAPER_TRADING,
        )

    # ── Order submission ───────────────────────────────────────────────────────

    def submit_order(
        self,
        symbol: str,
        qty: int,
        side: str,              # 'buy' | 'sell'
        order_type: str = "market",
        time_in_force: str = "day",
    ) -> Optional[str]:
        """
        Submit a market order via Alpaca.

        Returns the order_id string on success, None on failure.
        """
        if qty <= 0:
            logger.warning("submit_order: qty=%d ≤ 0 for %s — skipped", qty, symbol)
            return None

        if not config.ALPACA_API_KEY:
            # Paper-sim mode: fake an order for testing without credentials
            order_id = f"SIM-{symbol}-{datetime.utcnow().strftime('%H%M%S%f')}"
            logger.info("SIM ORDER %s %d %s → %s", side.upper(), qty, symbol, order_id)
            self._record_fill(symbol, qty, side, order_id, fill_price=None)
            return order_id

        try:
            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
            tif = TimeInForce.DAY if time_in_force == "day" else TimeInForce.GTC

            req = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=order_side,
                time_in_force=tif,
            )
            client = self._trading_client()
            order = client.submit_order(req)
            order_id = str(order.id)
            logger.info(
                "ORDER SUBMITTED: %s %d %s (id=%s)", side.upper(), qty, symbol, order_id
            )
            self._pending_orders[order_id] = {
                "symbol": symbol,
                "qty": qty,
                "side": side,
                "submitted_at": datetime.utcnow(),
            }
            return order_id
        except Exception as e:
            logger.error("Order submission failed for %s: %s", symbol, e)
            return None

    def _record_fill(
        self,
        symbol: str,
        qty: int,
        side: str,
        order_id: str,
        fill_price: Optional[float],
    ) -> None:
        """Update internal state + write to orders.csv after a fill."""
        now = datetime.now(tz=ET)
        price = fill_price or 0.0

        if side.lower() == "buy":
            self.open_positions[symbol] = {
                "entry_price": price,
                "qty": qty,
                "side": "long",
                "order_id": order_id,
                "entry_time": now,
                "unrealized_pnl": 0.0,
            }
        else:
            # Closing an existing position — handled by ExitManager, just log
            pass

        self.log_order(symbol, qty, side, price, order_id, now)

    # ── Order status / fill reconciliation ────────────────────────────────────

    def get_order_status(self, order_id: str) -> dict:
        """Fetch live order status from Alpaca."""
        if not config.ALPACA_API_KEY:
            return {"status": "filled", "filled_qty": None, "filled_avg_price": None}

        try:
            from alpaca.trading.requests import GetOrderByIdRequest
            client = self._trading_client()
            order = client.get_order_by_id(order_id)
            return {
                "status": str(order.status),
                "filled_qty": float(order.filled_qty or 0),
                "filled_avg_price": float(order.filled_avg_price or 0),
            }
        except Exception as e:
            logger.error("get_order_status failed for %s: %s", order_id, e)
            return {"status": "unknown", "filled_qty": None, "filled_avg_price": None}

    def check_and_update_fills(self) -> None:
        """Poll pending orders and promote filled ones to open_positions."""
        if not config.ALPACA_API_KEY:
            return

        filled = []
        for order_id, meta in list(self._pending_orders.items()):
            status = self.get_order_status(order_id)
            if status["status"] == "filled":
                self._record_fill(
                    meta["symbol"],
                    meta["qty"],
                    meta["side"],
                    order_id,
                    status["filled_avg_price"],
                )
                filled.append(order_id)
                logger.info(
                    "FILL: %s %s × %d @ %.4f",
                    meta["side"].upper(), meta["symbol"],
                    meta["qty"], status["filled_avg_price"] or 0,
                )
        for oid in filled:
            del self._pending_orders[oid]

    def cancel_order(self, order_id: str) -> bool:
        if not config.ALPACA_API_KEY:
            return True
        try:
            client = self._trading_client()
            client.cancel_order_by_id(order_id)
            logger.info("Cancelled order %s", order_id)
            return True
        except Exception as e:
            logger.error("Cancel failed for %s: %s", order_id, e)
            return False

    def get_position(self, symbol: str) -> Optional[dict]:
        return self.open_positions.get(symbol)

    def update_unrealized_pnl(self, symbol: str, current_price: float) -> None:
        pos = self.open_positions.get(symbol)
        if pos and pos["entry_price"] > 0:
            sign = 1 if pos["side"] == "long" else -1
            pct = sign * (current_price - pos["entry_price"]) / pos["entry_price"]
            pos["unrealized_pnl"] = round(pct, 6)

    # ── Logging ────────────────────────────────────────────────────────────────

    def log_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        entry_price: float,
        order_id: str,
        timestamp: datetime,
    ) -> None:
        row = {
            "timestamp": timestamp.isoformat(),
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "entry_price": entry_price,
            "order_id": order_id,
        }
        _write_csv_row(config.ORDERS_CSV, row)


# ── ExitManager ────────────────────────────────────────────────────────────────

class ExitManager:
    """Monitor open positions and trigger exits based on TP / SL / time."""

    def __init__(self, order_manager: OrderManager):
        self.om = order_manager

    def check_take_profit(self, symbol: str, current_price: float) -> bool:
        pos = self.om.get_position(symbol)
        if not pos or pos["entry_price"] <= 0:
            return False
        sign = 1 if pos["side"] == "long" else -1
        pct_change = sign * (current_price - pos["entry_price"]) / pos["entry_price"]
        return pct_change >= config.TAKE_PROFIT_PCT

    def check_stop_loss(self, symbol: str, current_price: float) -> bool:
        pos = self.om.get_position(symbol)
        if not pos or pos["entry_price"] <= 0:
            return False
        sign = 1 if pos["side"] == "long" else -1
        pct_change = sign * (current_price - pos["entry_price"]) / pos["entry_price"]
        return pct_change <= config.STOP_LOSS_PCT

    def check_time_exit(
        self, symbol: str, current_time: Optional[datetime] = None
    ) -> bool:
        now = current_time or datetime.now(tz=ET)
        if not isinstance(now.tzinfo, type(ET)):
            now = now.astimezone(ET)
        from risk_manager import _MARKET_CLOSE
        return now.time() >= _MARKET_CLOSE

    def check_max_hold(
        self, symbol: str, current_time: Optional[datetime] = None
    ) -> bool:
        pos = self.om.get_position(symbol)
        if not pos:
            return False
        now = current_time or datetime.now(tz=ET)
        entry = pos["entry_time"]
        if not entry.tzinfo:
            entry = ET.localize(entry)
        hold_days = (now - entry).total_seconds() / 86400
        return hold_days >= config.MAX_HOLD_DAYS

    def execute_exit(
        self,
        symbol: str,
        current_price: float,
        exit_reason: str,
    ) -> Optional[str]:
        """
        Close position for symbol, log to trades.csv, remove from open_positions.

        Returns the exit order_id.
        """
        pos = self.om.get_position(symbol)
        if not pos:
            logger.warning("execute_exit: no position found for %s", symbol)
            return None

        exit_side = "sell" if pos["side"] == "long" else "buy"
        order_id = self.om.submit_order(
            symbol=symbol,
            qty=pos["qty"],
            side=exit_side,
            order_type="market",
            time_in_force="day",
        )

        entry_price = pos["entry_price"]
        sign = 1 if pos["side"] == "long" else -1
        pnl_pct = (
            sign * (current_price - entry_price) / entry_price
            if entry_price > 0 else 0.0
        )
        entry_time = pos["entry_time"]
        now = datetime.now(tz=ET)
        hold_hours = (now - entry_time).total_seconds() / 3600 if entry_time else 0

        self._log_trade(
            symbol=symbol,
            entry_date=entry_time.isoformat() if entry_time else "",
            entry_price=entry_price,
            exit_date=now.isoformat(),
            exit_price=current_price,
            pnl_pct=round(pnl_pct, 6),
            hold_hours=round(hold_hours, 2),
            exit_reason=exit_reason,
            side=pos["side"],
        )

        del self.om.open_positions[symbol]
        logger.info(
            "EXIT %s | reason=%s | pnl=%.2f%%",
            symbol, exit_reason, pnl_pct * 100,
        )
        return order_id

    def _log_trade(self, **kwargs) -> None:
        row = {
            "timestamp": datetime.now(tz=ET).isoformat(),
            **kwargs,
        }
        _write_csv_row(config.TRADES_CSV, row)

    def run_exit_checks(
        self,
        symbol: str,
        current_price: float,
        current_time: Optional[datetime] = None,
    ) -> Optional[str]:
        """
        Run all exit checks in priority order.

        Returns the exit_reason string if an exit was triggered, else None.
        """
        if self.check_time_exit(symbol, current_time):
            self.execute_exit(symbol, current_price, "time_exit")
            return "time_exit"
        if self.check_stop_loss(symbol, current_price):
            self.execute_exit(symbol, current_price, "stop_loss")
            return "stop_loss"
        if self.check_take_profit(symbol, current_price):
            self.execute_exit(symbol, current_price, "take_profit")
            return "take_profit"
        if self.check_max_hold(symbol, current_time):
            self.execute_exit(symbol, current_price, "max_hold")
            return "max_hold"
        return None
