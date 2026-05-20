"""
ORB Executor — Alpaca order management for the Opening Range Breakout strategy.

Lifecycle per trading day:
  1. 9:35 AM  → submit_entry_orders()   : stop orders at ORB high/low
  2. ~9:35–15:50 → sync_fills()         : detect fills, attach stop-loss orders
  3. 15:50 PM → close_all_positions()   : market-close any open positions
  4. 15:50 PM → cancel_pending_entries(): cancel any unfilled stop-entry orders
"""

import csv
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytz

import config
from strategies.orb.strategy import ORBSignal, Direction, compute_shares
from strategies.orb import orb_config

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

ORB_ORDERS_CSV = orb_config.orders_csv()
ORB_TRADES_CSV = orb_config.trades_csv()


# ── CSV helper ─────────────────────────────────────────────────────────────────

def _append_csv(path: Path, row: dict) -> None:
    write_header = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=row.keys())
        if write_header:
            writer.writeheader()
        writer.writerow(row)


# ── Internal state dataclasses ─────────────────────────────────────────────────

@dataclass
class PendingEntry:
    """A stop-entry order that has been submitted but not yet filled."""
    order_id: str
    signal: ORBSignal
    qty: int
    submitted_at: datetime


@dataclass
class OpenPosition:
    """An ORB entry that has been filled and is currently held."""
    ticker: str
    direction: Direction
    entry_price: float
    qty: int
    stop_loss_price: float
    stop_loss_order_id: Optional[str]
    take_profit_price: float
    take_profit_order_id: Optional[str]
    entry_time: datetime


# ── ORBExecutor ────────────────────────────────────────────────────────────────

class ORBExecutor:
    """
    Manages the full order lifecycle for the ORB strategy via Alpaca.

    pending_entries : order_id → PendingEntry  (stop orders not yet triggered)
    open_positions  : ticker   → OpenPosition  (filled entries still in market)
    """

    def __init__(self, capital: float):
        self.capital = capital
        self.pending_entries: dict[str, PendingEntry] = {}
        self.open_positions: dict[str, OpenPosition] = {}

    # ── Alpaca client ──────────────────────────────────────────────────────────

    def _client(self):
        from alpaca.trading.client import TradingClient
        return TradingClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
            paper=config.PAPER_TRADING,
        )

    # ── Step 1: submit stop-entry orders at 9:35 AM ───────────────────────────

    def submit_entry_orders(self, signals: list[ORBSignal]) -> None:
        """
        For each ORBSignal submit a stop order at the opening range boundary.
        LONG  → stop buy  order at signal.entry_price (5-min high)
        SHORT → stop sell order at signal.entry_price (5-min low)
        """
        for signal in signals:
            qty = compute_shares(
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                capital=self.capital,
            )
            if qty <= 0:
                logger.warning("ORB: qty=0 for %s — skipping", signal.ticker)
                continue

            order_id = self._submit_stop_order(
                ticker=signal.ticker,
                qty=qty,
                side="buy" if signal.direction == Direction.LONG else "sell",
                stop_price=signal.entry_price,
            )
            if order_id:
                self.pending_entries[order_id] = PendingEntry(
                    order_id=order_id,
                    signal=signal,
                    qty=qty,
                    submitted_at=datetime.now(tz=ET),
                )
                self._log_order(
                    ticker=signal.ticker,
                    order_type="stop_entry",
                    side="buy" if signal.direction == Direction.LONG else "sell",
                    qty=qty,
                    price=signal.entry_price,
                    order_id=order_id,
                    status="submitted",
                )

    def _submit_stop_order(
        self,
        ticker: str,
        qty: int,
        side: str,
        stop_price: float,
    ) -> Optional[str]:
        """Submit a stop (entry) order. Returns order_id or None on failure."""
        if not config.ALPACA_API_KEY:
            order_id = f"SIM-ENTRY-{ticker}-{datetime.utcnow().strftime('%H%M%S%f')}"
            logger.info("SIM STOP %s %d %s @ stop=%.4f → %s", side.upper(), qty, ticker, stop_price, order_id)
            return order_id

        try:
            from alpaca.trading.requests import StopOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            req = StopOrderRequest(
                symbol=ticker,
                qty=qty,
                side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                stop_price=round(stop_price, 2),
            )
            order = self._client().submit_order(req)
            logger.info("STOP ENTRY submitted: %s %d %s @ %.4f (id=%s)", side.upper(), qty, ticker, stop_price, order.id)
            return str(order.id)
        except Exception as e:
            logger.error("Failed to submit stop entry for %s: %s", ticker, e)
            return None

    # ── Step 2: detect fills and attach stop-loss orders ──────────────────────

    def sync_fills(self) -> None:
        """
        Poll all pending entry orders. For any that have filled:
          - Move them to open_positions
          - Submit a stop-loss order at signal.stop_loss
        """
        if not self.pending_entries:
            return

        filled_ids = []
        for order_id, pending in list(self.pending_entries.items()):
            status = self._get_order_status(order_id)
            if status["status"] == "filled":
                fill_price = status["filled_avg_price"] or pending.signal.entry_price
                sl_order_id = self._submit_stop_loss(
                    ticker=pending.signal.ticker,
                    qty=pending.qty,
                    direction=pending.signal.direction,
                    stop_price=pending.signal.stop_loss,
                )
                tp_order_id = self._submit_take_profit(
                    ticker=pending.signal.ticker,
                    qty=pending.qty,
                    direction=pending.signal.direction,
                    limit_price=pending.signal.take_profit,
                )
                self.open_positions[pending.signal.ticker] = OpenPosition(
                    ticker=pending.signal.ticker,
                    direction=pending.signal.direction,
                    entry_price=fill_price,
                    qty=pending.qty,
                    stop_loss_price=pending.signal.stop_loss,
                    stop_loss_order_id=sl_order_id,
                    take_profit_price=pending.signal.take_profit,
                    take_profit_order_id=tp_order_id,
                    entry_time=datetime.now(tz=ET),
                )
                self._log_order(
                    ticker=pending.signal.ticker,
                    order_type="stop_entry",
                    side="buy" if pending.signal.direction == Direction.LONG else "sell",
                    qty=pending.qty,
                    price=fill_price,
                    order_id=order_id,
                    status="filled",
                )
                logger.info("ENTRY FILLED: %s %s × %d @ %.4f", pending.signal.direction.value, pending.signal.ticker, pending.qty, fill_price)
                filled_ids.append(order_id)

        for oid in filled_ids:
            del self.pending_entries[oid]

    def _submit_stop_loss(
        self,
        ticker: str,
        qty: int,
        direction: Direction,
        stop_price: float,
    ) -> Optional[str]:
        """Submit a stop-loss order on the opposite side of the entry."""
        exit_side = "sell" if direction == Direction.LONG else "buy"

        if not config.ALPACA_API_KEY:
            order_id = f"SIM-SL-{ticker}-{datetime.utcnow().strftime('%H%M%S%f')}"
            logger.info("SIM STOP-LOSS %s %d %s @ %.4f → %s", exit_side.upper(), qty, ticker, stop_price, order_id)
            self._log_order(ticker, "stop_loss", exit_side, qty, stop_price, order_id, "submitted")
            return order_id

        try:
            from alpaca.trading.requests import StopOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            req = StopOrderRequest(
                symbol=ticker,
                qty=qty,
                side=OrderSide.BUY if exit_side == "buy" else OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                stop_price=round(stop_price, 2),
            )
            order = self._client().submit_order(req)
            logger.info("STOP LOSS submitted: %s %d %s @ %.4f (id=%s)", exit_side.upper(), qty, ticker, stop_price, order.id)
            self._log_order(ticker, "stop_loss", exit_side, qty, stop_price, str(order.id), "submitted")
            return str(order.id)
        except Exception as e:
            logger.error("Failed to submit stop loss for %s: %s", ticker, e)
            return None

    def _submit_take_profit(
        self,
        ticker: str,
        qty: int,
        direction: Direction,
        limit_price: float,
    ) -> Optional[str]:
        """Submit a limit order at the take-profit level."""
        exit_side = "sell" if direction == Direction.LONG else "buy"

        if not config.ALPACA_API_KEY:
            order_id = f"SIM-TP-{ticker}-{datetime.utcnow().strftime('%H%M%S%f')}"
            logger.info("SIM TAKE-PROFIT %s %d %s @ %.4f → %s", exit_side.upper(), qty, ticker, limit_price, order_id)
            self._log_order(ticker, "take_profit", exit_side, qty, limit_price, order_id, "submitted")
            return order_id

        try:
            from alpaca.trading.requests import LimitOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            req = LimitOrderRequest(
                symbol=ticker,
                qty=qty,
                side=OrderSide.SELL if exit_side == "sell" else OrderSide.BUY,
                time_in_force=TimeInForce.DAY,
                limit_price=round(limit_price, 2),
            )
            order = self._client().submit_order(req)
            logger.info("TAKE PROFIT submitted: %s %d %s @ %.4f (id=%s)", exit_side.upper(), qty, ticker, limit_price, order.id)
            self._log_order(ticker, "take_profit", exit_side, qty, limit_price, str(order.id), "submitted")
            return str(order.id)
        except Exception as e:
            logger.error("Failed to submit take profit for %s: %s", ticker, e)
            return None

    def sync_take_profit_hits(self) -> None:
        """Check if any take-profit limit orders have been filled."""
        for ticker, pos in list(self.open_positions.items()):
            if not pos.take_profit_order_id:
                continue
            status = self._get_order_status(pos.take_profit_order_id)
            if status["status"] == "filled":
                fill_price = status["filled_avg_price"] or pos.take_profit_price
                if pos.stop_loss_order_id:
                    self._cancel_order(pos.stop_loss_order_id)
                pnl_r = self._calc_pnl_r(pos, exit_price=fill_price)
                hold_minutes = (datetime.now(tz=ET) - pos.entry_time).total_seconds() / 60
                self._log_trade(
                    ticker=ticker,
                    direction=pos.direction.value,
                    entry_price=pos.entry_price,
                    exit_price=fill_price,
                    stop_loss=pos.stop_loss_price,
                    qty=pos.qty,
                    pnl_r=pnl_r,
                    hold_minutes=round(hold_minutes, 1),
                    exit_reason="take_profit",
                    exit_order_id=pos.take_profit_order_id,
                )
                logger.info("TAKE PROFIT HIT: %s @ %.4f (pnl_r=%.2fR)", ticker, fill_price, pnl_r)
                del self.open_positions[ticker]

    # ── Step 3a: EOD — cancel all unfilled stop-entry orders ──────────────────

    def cancel_pending_entries(self) -> None:
        """Cancel all stop-entry orders that were never triggered."""
        for order_id, pending in list(self.pending_entries.items()):
            self._cancel_order(order_id)
            logger.info("Cancelled unfilled entry order for %s (id=%s)", pending.signal.ticker, order_id)
            self._log_order(
                ticker=pending.signal.ticker,
                order_type="stop_entry",
                side="buy" if pending.signal.direction == Direction.LONG else "sell",
                qty=pending.qty,
                price=pending.signal.entry_price,
                order_id=order_id,
                status="cancelled",
            )
        self.pending_entries.clear()

    # ── Step 3b: EOD — market-close all open positions ────────────────────────

    def close_all_positions(self) -> None:
        """
        Submit market orders to close every open position.
        Cancels the corresponding stop-loss order first to avoid double-exit.
        """
        for ticker, pos in list(self.open_positions.items()):
            if pos.stop_loss_order_id:
                self._cancel_order(pos.stop_loss_order_id)
            if pos.take_profit_order_id:
                self._cancel_order(pos.take_profit_order_id)

            exit_side = "sell" if pos.direction == Direction.LONG else "buy"
            order_id = self._submit_market_order(ticker, pos.qty, exit_side)

            now = datetime.now(tz=ET)
            hold_minutes = (now - pos.entry_time).total_seconds() / 60
            pnl_r = self._calc_pnl_r(pos)

            self._log_trade(
                ticker=ticker,
                direction=pos.direction.value,
                entry_price=pos.entry_price,
                exit_price=None,   # fill price unknown until reconciled; use None
                stop_loss=pos.stop_loss_price,
                qty=pos.qty,
                pnl_r=pnl_r,
                hold_minutes=round(hold_minutes, 1),
                exit_reason="eod",
                exit_order_id=order_id or "",
            )
            logger.info("EOD CLOSE: %s %s × %d (pnl_r=%.2f)", pos.direction.value, ticker, pos.qty, pnl_r)

        self.open_positions.clear()

    # ── Stop-loss fill detection (optional — call in same sync loop) ──────────

    def sync_stop_loss_hits(self) -> None:
        """
        Check if any stop-loss orders have been filled by Alpaca.
        Removes the position and logs the trade.
        """
        for ticker, pos in list(self.open_positions.items()):
            if not pos.stop_loss_order_id:
                continue
            status = self._get_order_status(pos.stop_loss_order_id)
            if status["status"] == "filled":
                fill_price = status["filled_avg_price"] or pos.stop_loss_price
                if pos.take_profit_order_id:
                    self._cancel_order(pos.take_profit_order_id)
                pnl_r = self._calc_pnl_r(pos, exit_price=fill_price)
                hold_minutes = (datetime.now(tz=ET) - pos.entry_time).total_seconds() / 60
                self._log_trade(
                    ticker=ticker,
                    direction=pos.direction.value,
                    entry_price=pos.entry_price,
                    exit_price=fill_price,
                    stop_loss=pos.stop_loss_price,
                    qty=pos.qty,
                    pnl_r=pnl_r,
                    hold_minutes=round(hold_minutes, 1),
                    exit_reason="stop_loss",
                    exit_order_id=pos.stop_loss_order_id,
                )
                logger.info("STOP LOSS HIT: %s @ %.4f (pnl_r=%.2fR)", ticker, fill_price, pnl_r)
                del self.open_positions[ticker]

    # ── Alpaca helpers ─────────────────────────────────────────────────────────

    def _submit_market_order(self, ticker: str, qty: int, side: str) -> Optional[str]:
        if not config.ALPACA_API_KEY:
            order_id = f"SIM-EOD-{ticker}-{datetime.utcnow().strftime('%H%M%S%f')}"
            logger.info("SIM MARKET %s %d %s → %s", side.upper(), qty, ticker, order_id)
            return order_id
        try:
            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce
            req = MarketOrderRequest(
                symbol=ticker,
                qty=qty,
                side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
            order = self._client().submit_order(req)
            return str(order.id)
        except Exception as e:
            logger.error("Market order failed for %s: %s", ticker, e)
            return None

    def _get_order_status(self, order_id: str) -> dict:
        if not config.ALPACA_API_KEY or order_id.startswith("SIM-"):
            return {"status": "filled", "filled_avg_price": None}
        try:
            order = self._client().get_order_by_id(order_id)
            return {
                "status": str(order.status),
                "filled_avg_price": float(order.filled_avg_price or 0) or None,
            }
        except Exception as e:
            logger.error("get_order_status failed for %s: %s", order_id, e)
            return {"status": "unknown", "filled_avg_price": None}

    def _cancel_order(self, order_id: str) -> None:
        if not config.ALPACA_API_KEY or order_id.startswith("SIM-"):
            return
        try:
            self._client().cancel_order_by_id(order_id)
        except Exception as e:
            logger.error("Cancel failed for %s: %s", order_id, e)

    # ── P&L helper ─────────────────────────────────────────────────────────────

    def _calc_pnl_r(self, pos: OpenPosition, exit_price: Optional[float] = None) -> float:
        """Express P&L in R units (multiples of the initial risk per share)."""
        risk_per_share = abs(pos.entry_price - pos.stop_loss_price)
        if risk_per_share == 0:
            return 0.0
        price_out = exit_price or pos.entry_price  # conservative if unknown
        sign = 1 if pos.direction == Direction.LONG else -1
        return round(sign * (price_out - pos.entry_price) / risk_per_share, 4)

    # ── CSV logging ────────────────────────────────────────────────────────────

    def _log_order(
        self,
        ticker: str,
        order_type: str,
        side: str,
        qty: int,
        price: float,
        order_id: str,
        status: str,
    ) -> None:
        _append_csv(ORB_ORDERS_CSV, {
            "timestamp": datetime.now(tz=ET).isoformat(),
            "ticker": ticker,
            "order_type": order_type,
            "side": side,
            "qty": qty,
            "price": price,
            "order_id": order_id,
            "status": status,
        })

    def _log_trade(self, **kwargs) -> None:
        row = {"timestamp": datetime.now(tz=ET).isoformat(), **kwargs}
        _append_csv(ORB_TRADES_CSV, row)
        _append_csv(orb_config.daily_trades_log(), row)
