"""
risk_manager.py — Position limits, market hours, and kill-switch logic.
"""

import logging
from datetime import datetime, time
from typing import Optional

import pytz

import config

logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")

_MARKET_OPEN = time(
    config.MARKET_OPEN_HOUR,
    config.MARKET_OPEN_MINUTE,
)
_MARKET_CLOSE = time(
    config.MARKET_CLOSE_HOUR,
    config.MARKET_CLOSE_MINUTE,
)


class RiskManager:
    """
    Stateless risk checks — pass current portfolio state in on each call
    rather than storing it here, so the risk layer stays side-effect–free.
    """

    # ── Market hours ───────────────────────────────────────────────────────────

    def check_market_hours(self, current_time: Optional[datetime] = None) -> bool:
        """
        Return True when current Eastern Time is inside trading window
        (9:30 AM – 3:50 PM ET, Mon–Fri).
        """
        now = current_time or datetime.now(tz=ET)
        if not isinstance(now.tzinfo, type(ET)):
            now = now.astimezone(ET)

        if now.weekday() >= 5:   # Saturday=5, Sunday=6
            return False

        t = now.time()
        return _MARKET_OPEN <= t <= _MARKET_CLOSE

    def check_day_trade_close(self, current_time: Optional[datetime] = None) -> bool:
        """
        Return True (→ force-close all positions) when time is at or past 3:50 PM ET.
        """
        now = current_time or datetime.now(tz=ET)
        if not isinstance(now.tzinfo, type(ET)):
            now = now.astimezone(ET)
        return now.time() >= _MARKET_CLOSE

    # ── Position limits ────────────────────────────────────────────────────────

    def check_position_limits(
        self,
        symbol: str,
        direction: int,
        size: int,
        open_positions: dict,
    ) -> bool:
        """
        Return True (→ order allowed) if:
          1. No existing position in the same symbol.
          2. Portfolio has fewer than MAX_OPEN_POSITIONS open.

        Parameters
        ----------
        symbol         : ticker to trade
        direction      : +1 (long) or -1 (short)
        size           : intended share count
        open_positions : dict mapping symbol → position dict
        """
        if size <= 0:
            logger.info("Position size 0 for %s — skipping", symbol)
            return False

        if symbol in open_positions:
            logger.info(
                "%s already has an open position — skipping new entry", symbol
            )
            return False

        if len(open_positions) >= config.MAX_OPEN_POSITIONS:
            logger.warning(
                "Max open positions (%d) reached — skipping %s",
                config.MAX_OPEN_POSITIONS, symbol,
            )
            return False

        return True

    # ── Max drawdown kill switch ───────────────────────────────────────────────

    def check_max_drawdown(self, daily_pnl_list: list[float]) -> bool:
        """
        Return True (→ STOP TRADING) when cumulative daily P&L has breached
        the maximum drawdown threshold (config.MAX_DAILY_DRAWDOWN_PCT).

        Parameters
        ----------
        daily_pnl_list : sequence of realised P&L values for the day
                         (positive = profit, negative = loss)
        """
        if not daily_pnl_list:
            return False

        cumulative = sum(daily_pnl_list)
        if cumulative < -abs(config.MAX_DAILY_DRAWDOWN_PCT * 100):
            logger.critical(
                "KILL SWITCH: daily P&L %.2f%% exceeds max drawdown %.1f%%",
                cumulative, config.MAX_DAILY_DRAWDOWN_PCT * 100,
            )
            return True
        return False

    def check_max_drawdown_dollar(
        self,
        daily_pnl_dollars: float,
        portfolio_value: float,
    ) -> bool:
        """
        Dollar-based kill switch: halt if loss exceeds MAX_DAILY_DRAWDOWN_PCT
        of portfolio value.
        """
        if portfolio_value <= 0:
            return False
        drawdown_pct = daily_pnl_dollars / portfolio_value
        if drawdown_pct < -config.MAX_DAILY_DRAWDOWN_PCT:
            logger.critical(
                "KILL SWITCH: daily P&L $%.2f (%.2f%%) breaches %.1f%% limit",
                daily_pnl_dollars, drawdown_pct * 100,
                config.MAX_DAILY_DRAWDOWN_PCT * 100,
            )
            return True
        return False
