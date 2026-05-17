"""
signals.py — Regime classification and mean reversion signal generation.

Three classes:
  RegimeClassifier  — stock-level volatility regime (low / medium / high)
  VIXGate           — market-level VIX regime and position multiplier
  MeanReversionSignal — Z-score signal on intraday bars
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

import config
from data_manager import get_vix

logger = logging.getLogger(__name__)


# ── RegimeClassifier ───────────────────────────────────────────────────────────

class RegimeClassifier:
    """Classify a single stock's volatility regime from daily bar history."""

    def compute_realized_vol(
        self,
        symbol: str,
        daily_bars: pd.DataFrame,
        lookback: int = 20,
    ) -> Optional[float]:
        """
        Annualized realized volatility = std(daily_returns, lookback) * sqrt(252).

        Returns None when there is insufficient data.
        """
        if daily_bars is None or daily_bars.empty:
            logger.warning("%s: empty daily bars for vol calc", symbol)
            return None

        closes = daily_bars["close"].dropna()
        if len(closes) < lookback + 1:
            logger.warning(
                "%s: need %d daily closes, got %d", symbol, lookback + 1, len(closes)
            )
            return None

        returns = closes.pct_change().dropna().tail(lookback)
        if len(returns) < lookback:
            return None

        realized_vol = returns.std() * np.sqrt(252)
        logger.debug("%s: realized_vol=%.4f", symbol, realized_vol)
        return float(realized_vol)

    def classify_stock_regime(self, realized_vol: Optional[float]) -> str:
        """
        Map a realized vol number to a regime label.

        Returns 'high_vol' when vol is unknown (conservative default).
        """
        if realized_vol is None:
            return "high_vol"
        if realized_vol < config.REALIZED_VOL_LOW_THRESHOLD:
            return "low_vol"
        if realized_vol < config.REALIZED_VOL_HIGH_THRESHOLD:
            return "medium_vol"
        return "high_vol"

    def get_regime_multiplier(self, regime: str) -> float:
        """Return the share-count multiplier for a given stock regime."""
        return config.STOCK_REGIME_MULTIPLIERS.get(regime, 0.0)

    def classify(
        self,
        symbol: str,
        daily_bars: pd.DataFrame,
        lookback: int = 20,
    ) -> dict:
        """
        Full pipeline: bars → vol → regime → multiplier.

        Returns a dict with keys: symbol, realized_vol, regime, multiplier.
        """
        rv = self.compute_realized_vol(symbol, daily_bars, lookback)
        regime = self.classify_stock_regime(rv)
        mult = self.get_regime_multiplier(regime)
        return {
            "symbol": symbol,
            "realized_vol": rv,
            "regime": regime,
            "multiplier": mult,
        }


# ── VIXGate ────────────────────────────────────────────────────────────────────

class VIXGate:
    """Market-level regime gating via VIX."""

    def get_vix(self) -> float:
        """Delegate to data_manager (handles caching + fallback)."""
        return get_vix()

    def classify_market_regime(self, vix: float) -> str:
        if vix < config.VIX_LOW_THRESHOLD:
            return "low_vol"
        if vix <= config.VIX_HIGH_THRESHOLD:
            return "medium_vol"
        return "high_vol"

    def get_position_size_multiplier(self, vix: float) -> float:
        """Return the VIX-based position size multiplier."""
        regime = self.classify_market_regime(vix)
        return config.VIX_POSITION_MULTIPLIERS.get(regime, 0.5)

    def gate(self) -> dict:
        """
        Fetch current VIX and return full regime info.

        Returns a dict with keys: vix, regime, position_multiplier.
        """
        vix = self.get_vix()
        regime = self.classify_market_regime(vix)
        mult = self.get_position_size_multiplier(vix)
        return {"vix": vix, "regime": regime, "position_multiplier": mult}


# ── MeanReversionSignal ────────────────────────────────────────────────────────

class MeanReversionSignal:
    """Z-score–based mean reversion signal on intraday (5-min) bars."""

    def compute_z_score(
        self,
        closes: pd.Series,
        lookback: int = config.LOOKBACK_BARS,
    ) -> Optional[float]:
        """
        Z = (close_t - SMA_t) / STD_t using a rolling window of `lookback` bars.

        Returns None when there are fewer bars than the lookback.
        """
        if closes is None or len(closes) < lookback:
            return None

        window = closes.tail(lookback)
        sma = window.mean()
        std = window.std(ddof=1)

        if std == 0 or np.isnan(std):
            return None

        return float((closes.iloc[-1] - sma) / std)

    def compute_rolling_stats(
        self,
        closes: pd.Series,
        lookback: int = config.LOOKBACK_BARS,
    ) -> tuple[Optional[float], Optional[float]]:
        """Return (sma_20, std_20) for the most recent window."""
        if len(closes) < lookback:
            return None, None
        window = closes.tail(lookback)
        return float(window.mean()), float(window.std(ddof=1))

    def generate_signal(
        self,
        symbol: str,
        intraday_bars: pd.DataFrame,
        stock_regime: str = "medium_vol",
        lookback: int = config.LOOKBACK_BARS,
    ) -> dict:
        """
        Main signal factory for one symbol.

        Parameters
        ----------
        symbol        : ticker string
        intraday_bars : DataFrame with at least a 'close' column
        stock_regime  : pre-computed stock regime string
        lookback      : number of bars for SMA/STD window

        Returns
        -------
        dict with keys:
          symbol, direction, z_score, confidence,
          stock_regime, timestamp, sma_20, std_20
        """
        base = {
            "symbol": symbol,
            "direction": 0,
            "z_score": None,
            "confidence": 0.0,
            "stock_regime": stock_regime,
            "timestamp": pd.Timestamp.utcnow(),
            "sma_20": None,
            "std_20": None,
        }

        if stock_regime == "high_vol":
            logger.info("%s: high_vol regime — no mean reversion signal", symbol)
            return base

        if intraday_bars is None or intraday_bars.empty:
            logger.warning("%s: no intraday bars for signal generation", symbol)
            return base

        closes = intraday_bars["close"].dropna()
        if len(closes) < lookback:
            logger.warning(
                "%s: need %d intraday bars, got %d", symbol, lookback, len(closes)
            )
            return base

        z = self.compute_z_score(closes, lookback)
        if z is None:
            return base

        sma, std = self.compute_rolling_stats(closes, lookback)
        threshold = config.Z_SCORE_ENTRY_THRESHOLD

        direction = 0
        if z < -threshold:
            direction = 1    # LONG: price well below mean, expect reversion up
        elif z > threshold:
            direction = -1   # SHORT: price well above mean, expect reversion down

        confidence = min(abs(z) / 3.0, 1.0)

        latest_ts = (
            intraday_bars.index[-1]
            if not intraday_bars.empty
            else pd.Timestamp.utcnow()
        )

        result = {
            "symbol": symbol,
            "direction": direction,
            "z_score": round(z, 4),
            "confidence": round(confidence, 4),
            "stock_regime": stock_regime,
            "timestamp": latest_ts,
            "sma_20": round(sma, 4) if sma is not None else None,
            "std_20": round(std, 6) if std is not None else None,
        }
        logger.info(
            "%s signal: direction=%+d  z=%.3f  regime=%s  conf=%.2f",
            symbol, direction, z, stock_regime, confidence,
        )
        return result

    def generate_signals_for_universe(
        self,
        symbol_bars: dict[str, pd.DataFrame],
        symbol_regimes: dict[str, str],
    ) -> list[dict]:
        """
        Convenience wrapper: generate signals for a full universe in one call.

        Parameters
        ----------
        symbol_bars    : {symbol: intraday_bars DataFrame}
        symbol_regimes : {symbol: regime_str}

        Returns
        -------
        List of signal dicts (one per symbol).
        """
        signals = []
        for symbol in config.SYMBOLS:
            bars = symbol_bars.get(symbol, pd.DataFrame())
            regime = symbol_regimes.get(symbol, "high_vol")
            sig = self.generate_signal(symbol, bars, stock_regime=regime)
            signals.append(sig)
        return signals
