"""
live_trader.py — Live order management for the ORB QQQ/TQQQ strategy.

Session timeline:
  9:35 AM  generate_signals()  — fetch first candle + ATR14, build signals
  9:35 AM  submit_entries()    — market orders for each signal
  every 60s sync()             — fill detection → attach stop-loss; check SL hits
  15:55 PM close_all()         — market-close all open positions
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import pytz

import config
from . import orb_qqq_config as cfg
from .strategy import Direction, ORBQQQSignal, generate_signal

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

ORDERS_CSV = config.OUTPUTS_DIR / "orb_qqq_orders.csv"
TRADES_CSV = config.OUTPUTS_DIR / "orb_qqq_trades.csv"
STATE_FILE = config.DATA_DIR / "orb_qqq_state.json"


def _append_csv(path: Path, row: dict) -> None:
    write_header = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=row.keys())
        if write_header:
            writer.writeheader()
        writer.writerow(row)


@dataclass
class LivePosition:
    symbol:          str
    direction:       Direction
    entry_price:     float
    stop:            float
    qty:             int
    entry_order_id:  str
    entry_time:      datetime
    risk_per_share:  float
    stop_order_id:   Optional[str] = None


class OrbQQQLiveTrader:
    """
    Manages live Alpaca orders for the ORB QQQ/TQQQ strategy.
    Uses optimised variant (ATR stop, EOD exit) by default.
    """

    def __init__(self, capital: float, variant: str = "optimised",
                 instruments: list[str] | None = None):
        self.capital     = capital
        self.variant     = variant
        self.instruments = instruments or ["TQQQ"]   # trade TQQQ only by default
        self.positions: dict[str, LivePosition]  = {}
        self.signals:   dict[str, ORBQQQSignal]  = {}
        self._atr_cache: dict[str, float]        = {}

    # ── Alpaca clients ──────────────────────────────────────────────────────────

    def _trading_client(self):
        from alpaca.trading.client import TradingClient
        return TradingClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
            paper=config.PAPER_TRADING,
        )

    def _data_client(self):
        from alpaca.data.historical import StockHistoricalDataClient
        return StockHistoricalDataClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
        )

    # ── Data helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_atr(df: pd.DataFrame, period: int = 14) -> float:
        high  = df["high"].values
        low   = df["low"].values
        close = df["close"].values
        tr = [
            max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
            for i in range(1, len(close))
        ]
        if len(tr) < period:
            return 0.0
        atr = sum(tr[:period]) / period
        for val in tr[period:]:
            atr = (atr * (period - 1) + val) / period
        return round(atr, 4)

    def fetch_atr14(self, symbol: str) -> float:
        """Return 14-day ATR. Tries cached parquet first, then live Alpaca daily bars."""
        import datetime as dt

        # Cached parquet
        try:
            path = cfg.bars_path(symbol, "1day")
            if path.exists():
                daily = pd.read_parquet(path)
                if hasattr(daily.index, "tz") and daily.index.tz:
                    daily.index = daily.index.tz_localize(None)
                daily = daily.sort_index().tail(40)
                if len(daily) >= cfg.ATR_PERIOD + 1:
                    atr = self._compute_atr(daily, cfg.ATR_PERIOD)
                    if atr > 0:
                        self._atr_cache[symbol] = atr
                        return atr
        except Exception:
            pass

        # Live Alpaca daily bars
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame
            end_dt   = datetime.now(ET)
            start_dt = end_dt.replace(year=end_dt.year if end_dt.month > 1 else end_dt.year - 1,
                                      month=end_dt.month - 1 if end_dt.month > 1 else 12)
            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame.Day,
                start=start_dt.replace(tzinfo=None),
                end=end_dt.replace(tzinfo=None),
            )
            raw = self._data_client().get_stock_bars(req).df
            if not raw.empty:
                flat = raw.reset_index()
                if "symbol" in flat.columns:
                    flat = flat[flat["symbol"] == symbol]
                daily = flat.set_index("timestamp")[["open", "high", "low", "close"]]
                if len(daily) >= cfg.ATR_PERIOD + 1:
                    atr = self._compute_atr(daily, cfg.ATR_PERIOD)
                    if atr > 0:
                        self._atr_cache[symbol] = atr
                        return atr
        except Exception as e:
            logger.warning("ATR14 live fetch failed for %s: %s", symbol, e)

        return 0.0

    def _fetch_opening_candle(self, symbol: str) -> Optional[pd.Series]:
        """Fetch the 9:30–9:35 ET 5-min bar for today."""
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        from datetime import time as dtime

        today = datetime.now(ET).strftime("%Y-%m-%d")
        start = ET.localize(datetime.strptime(f"{today} 09:29:00", "%Y-%m-%d %H:%M:%S"))
        end   = ET.localize(datetime.strptime(f"{today} 09:40:00", "%Y-%m-%d %H:%M:%S"))

        try:
            req  = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame(5, TimeFrameUnit.Minute),
                start=start,
                end=end,
            )
            raw  = self._data_client().get_stock_bars(req).df
            if raw.empty:
                return None
            flat = raw.reset_index()
            if "symbol" in flat.columns:
                flat = flat[flat["symbol"] == symbol]
            flat["ts_et"] = pd.to_datetime(flat["timestamp"], utc=True).dt.tz_convert(ET)
            flat = flat.sort_values("ts_et")
            # Target: bar that starts at or after 09:30 and before 09:35
            mask = (flat["ts_et"].dt.time >= dtime(9, 30)) & (flat["ts_et"].dt.time < dtime(9, 35))
            row  = flat[mask]
            if row.empty:
                row = flat
            r = row.iloc[0]
            return pd.Series({"open": float(r["open"]), "high": float(r["high"]),
                               "low":  float(r["low"]),  "close": float(r["close"])})
        except Exception as e:
            logger.error("Opening candle fetch failed for %s: %s", symbol, e)
            return None

    def _fetch_c2_open(self, symbol: str) -> Optional[float]:
        """Fetch the open of the 9:35–9:40 ET 5-min bar (entry price)."""
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        from datetime import time as dtime

        today = datetime.now(ET).strftime("%Y-%m-%d")
        start = ET.localize(datetime.strptime(f"{today} 09:35:00", "%Y-%m-%d %H:%M:%S"))
        end   = ET.localize(datetime.strptime(f"{today} 09:45:00", "%Y-%m-%d %H:%M:%S"))

        try:
            req  = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame(5, TimeFrameUnit.Minute),
                start=start,
                end=end,
            )
            raw  = self._data_client().get_stock_bars(req).df
            if raw.empty:
                return None
            flat = raw.reset_index()
            if "symbol" in flat.columns:
                flat = flat[flat["symbol"] == symbol]
            flat["ts_et"] = pd.to_datetime(flat["timestamp"], utc=True).dt.tz_convert(ET)
            flat = flat.sort_values("ts_et")
            mask = flat["ts_et"].dt.time >= dtime(9, 35)
            row  = flat[mask]
            if row.empty:
                return None
            return float(row.iloc[0]["open"])
        except Exception:
            return None

    # ── Signal generation ───────────────────────────────────────────────────────

    def generate_signals(self) -> dict[str, ORBQQQSignal]:
        """Generate signals for all instruments. Returns {symbol: signal}."""
        self.signals = {}
        for symbol in self.instruments:
            logger.info("Generating signal for %s  variant=%s", symbol, self.variant)

            c1 = self._fetch_opening_candle(symbol)
            if c1 is None:
                logger.warning("%s: opening candle not available", symbol)
                continue

            atr14 = self.fetch_atr14(symbol) if self.variant == "optimised" else 0.0
            if self.variant == "optimised" and atr14 == 0.0:
                logger.warning("%s: ATR14=0 — cannot generate optimised signal", symbol)
                continue

            c2_open = self._fetch_c2_open(symbol) or float(c1["close"])

            sig = generate_signal(
                symbol=symbol, c1=c1, c2_open=c2_open,
                capital=self.capital, atr14=atr14, variant=self.variant,
            )
            if sig is None:
                logger.info("%s: no signal (doji or zero shares)", symbol)
                continue

            self.signals[symbol] = sig
            logger.info(
                "%s  %s  entry=%.4f  stop=%.4f  qty=%d  risk_$=%.2f  atr14=%.4f",
                symbol, sig.direction.value,
                sig.entry, sig.stop, sig.shares,
                sig.risk_per_share * sig.shares,
                atr14,
            )

        return self.signals

    # ── Order submission ────────────────────────────────────────────────────────

    def submit_entries(self) -> None:
        """Submit market entry orders for all pending signals."""
        for symbol, sig in self.signals.items():
            if symbol in self.positions:
                logger.warning("%s: position already exists, skipping", symbol)
                continue
            side     = "buy" if sig.direction == Direction.LONG else "sell"
            order_id = self._market_order(symbol, sig.shares, side)
            if not order_id:
                continue
            self.positions[symbol] = LivePosition(
                symbol=symbol, direction=sig.direction,
                entry_price=sig.entry, stop=sig.stop,
                qty=sig.shares, entry_order_id=order_id,
                entry_time=datetime.now(tz=ET),
                risk_per_share=sig.risk_per_share,
            )
            _append_csv(ORDERS_CSV, {
                "timestamp": datetime.now(tz=ET).isoformat(),
                "symbol": symbol, "order_type": "entry",
                "side": side, "qty": sig.shares,
                "price": sig.entry, "order_id": order_id, "status": "submitted",
            })
            logger.info("ENTRY submitted: %s %s × %d @ ~%.4f  id=%s",
                        side.upper(), symbol, sig.shares, sig.entry, order_id)

    # ── Sync loop ───────────────────────────────────────────────────────────────

    def sync(self) -> None:
        """Detect entry fills, attach stop-loss orders, check stop-loss hits."""
        self._attach_stop_losses()
        self._check_stop_hits()

    def _attach_stop_losses(self) -> None:
        """For positions without a SL order, check if entry filled and attach SL."""
        for symbol, pos in list(self.positions.items()):
            if pos.stop_order_id is not None:
                continue
            status = self._order_status(pos.entry_order_id)
            if status["status"] in ("filled", "partially_filled"):
                fill = status["filled_avg_price"] or pos.entry_price
                pos.entry_price = fill
                sl_side = "sell" if pos.direction == Direction.LONG else "buy"
                sl_id   = self._stop_order(symbol, pos.qty, sl_side, pos.stop)
                pos.stop_order_id = sl_id
                logger.info("FILLED %s %s × %d @ %.4f — SL @ %.4f (id=%s)",
                            pos.direction.value, symbol, pos.qty, fill, pos.stop, sl_id)

    def _check_stop_hits(self) -> None:
        """Log and remove positions whose stop-loss order has filled."""
        for symbol, pos in list(self.positions.items()):
            if not pos.stop_order_id:
                continue
            status = self._order_status(pos.stop_order_id)
            if status["status"] == "filled":
                fill = status["filled_avg_price"] or pos.stop
                pnl  = (fill - pos.entry_price) if pos.direction == Direction.LONG \
                       else (pos.entry_price - fill)
                mins = (datetime.now(tz=ET) - pos.entry_time).total_seconds() / 60
                _append_csv(TRADES_CSV, {
                    "timestamp": datetime.now(tz=ET).isoformat(),
                    "symbol": symbol, "direction": pos.direction.value,
                    "entry_price": pos.entry_price, "exit_price": fill,
                    "stop": pos.stop, "qty": pos.qty,
                    "pnl": round(pnl * pos.qty, 2),
                    "hold_minutes": round(mins, 1), "exit_reason": "stop_loss",
                })
                logger.info("STOP HIT %s @ %.4f  pnl=$%.2f", symbol, fill, pnl * pos.qty)
                del self.positions[symbol]

    # ── EOD close ────────────────────────────────────────────────────────────────

    def close_all(self) -> None:
        """Cancel stop-loss orders and market-close all open positions."""
        for symbol, pos in list(self.positions.items()):
            if pos.stop_order_id:
                self._cancel(pos.stop_order_id)
            side     = "sell" if pos.direction == Direction.LONG else "buy"
            order_id = self._market_order(symbol, pos.qty, side)
            mins     = (datetime.now(tz=ET) - pos.entry_time).total_seconds() / 60
            _append_csv(TRADES_CSV, {
                "timestamp": datetime.now(tz=ET).isoformat(),
                "symbol": symbol, "direction": pos.direction.value,
                "entry_price": pos.entry_price, "exit_price": None,
                "stop": pos.stop, "qty": pos.qty, "pnl": None,
                "hold_minutes": round(mins, 1), "exit_reason": "eod",
            })
            logger.info("EOD CLOSE %s %s × %d  order=%s", pos.direction.value, symbol, pos.qty, order_id)
        self.positions.clear()

    # ── State file ───────────────────────────────────────────────────────────────

    def write_state(self, account: dict, phase: str = "active") -> None:
        import json
        state = {
            "updated_at":   datetime.now(tz=ET).isoformat(),
            "phase":        phase,
            "variant":      self.variant,
            "instruments":  self.instruments,
            "atr_stop_pct": cfg.ATR_STOP_PCT,
            "account":      account,
            "signals": [
                {
                    "symbol":    s.symbol,
                    "direction": s.direction.value,
                    "entry":     s.entry,
                    "stop":      s.stop,
                    "shares":    s.shares,
                    "risk_usd":  round(s.risk_per_share * s.shares, 2),
                    "atr14":     round(self._atr_cache.get(s.symbol, 0.0), 4),
                }
                for s in self.signals.values()
            ],
            "positions": [
                {
                    "symbol":      p.symbol,
                    "direction":   p.direction.value,
                    "entry_price": round(p.entry_price, 4),
                    "stop":        round(p.stop, 4),
                    "qty":         p.qty,
                    "entry_time":  p.entry_time.isoformat(),
                    "has_sl":      p.stop_order_id is not None,
                }
                for p in self.positions.values()
            ],
        }
        STATE_FILE.write_text(json.dumps(state, indent=2))

    # ── Alpaca helpers ───────────────────────────────────────────────────────────

    def _market_order(self, symbol: str, qty: int, side: str) -> Optional[str]:
        if not config.ALPACA_API_KEY:
            oid = f"SIM-MKT-{symbol}-{datetime.utcnow().strftime('%H%M%S%f')}"
            logger.info("SIM MARKET %s %d %s → %s", side.upper(), qty, symbol, oid)
            return oid
        try:
            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce
            req   = MarketOrderRequest(
                symbol=symbol, qty=qty,
                side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
            order = self._trading_client().submit_order(req)
            return str(order.id)
        except Exception as e:
            logger.error("Market order failed for %s: %s", symbol, e)
            return None

    def _stop_order(self, symbol: str, qty: int, side: str, stop_price: float) -> Optional[str]:
        if not config.ALPACA_API_KEY:
            oid = f"SIM-SL-{symbol}-{datetime.utcnow().strftime('%H%M%S%f')}"
            logger.info("SIM STOP %s %d %s @ %.4f → %s", side.upper(), qty, symbol, stop_price, oid)
            return oid
        try:
            from alpaca.trading.requests import StopOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce
            req   = StopOrderRequest(
                symbol=symbol, qty=qty,
                side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                stop_price=round(stop_price, 2),
            )
            order = self._trading_client().submit_order(req)
            logger.info("STOP SL %s %d %s @ %.4f  id=%s", side.upper(), qty, symbol, stop_price, order.id)
            return str(order.id)
        except Exception as e:
            logger.error("Stop order failed for %s: %s", symbol, e)
            return None

    def _order_status(self, order_id: str) -> dict:
        if not config.ALPACA_API_KEY or order_id.startswith("SIM-"):
            return {"status": "filled", "filled_avg_price": None}
        try:
            order = self._trading_client().get_order_by_id(order_id)
            return {
                "status": str(order.status),
                "filled_avg_price": float(order.filled_avg_price or 0) or None,
            }
        except Exception as e:
            logger.error("Order status failed for %s: %s", order_id, e)
            return {"status": "unknown", "filled_avg_price": None}

    def _cancel(self, order_id: str) -> None:
        if not config.ALPACA_API_KEY or order_id.startswith("SIM-"):
            return
        try:
            self._trading_client().cancel_order_by_id(order_id)
        except Exception as e:
            logger.error("Cancel failed for %s: %s", order_id, e)
