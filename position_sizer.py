"""
position_sizer.py — Final share-count calculation with regime and VIX adjustments.
"""

import logging

import config
from signals import RegimeClassifier, VIXGate

logger = logging.getLogger(__name__)


class PositionSizer:
    """Compute final position size applying stock-regime and VIX multipliers."""

    def __init__(self):
        self._regime_classifier = RegimeClassifier()
        self._vix_gate = VIXGate()

    def compute_position_size(
        self,
        symbol: str,
        stock_regime: str,
        vix: float,
        base_size: int = config.BASE_POSITION_SIZE,
    ) -> int:
        """
        Final shares = floor(base_size × stock_mult × vix_mult).

        Returns 0 when high_vol stock regime → skip trade.
        """
        stock_mult = config.STOCK_REGIME_MULTIPLIERS.get(stock_regime, 0.0)
        vix_mult = self._vix_gate.get_position_size_multiplier(vix)

        raw = base_size * stock_mult * vix_mult
        final = int(raw)  # floor to whole shares

        logger.debug(
            "%s size: base=%d × stock_mult=%.2f × vix_mult=%.2f → %d shares",
            symbol, base_size, stock_mult, vix_mult, final,
        )
        return final

    def calculate_sizing_breakdown(
        self,
        symbol: str,
        stock_regime: str,
        vix: float,
        base_size: int = config.BASE_POSITION_SIZE,
    ) -> dict:
        """
        Return full breakdown dict for logging / inspection:

          base_size, stock_regime, stock_regime_mult,
          vix, vix_regime, vix_mult,
          raw_size, final_size
        """
        stock_mult = config.STOCK_REGIME_MULTIPLIERS.get(stock_regime, 0.0)
        vix_mult = self._vix_gate.get_position_size_multiplier(vix)
        vix_regime = self._vix_gate.classify_market_regime(vix)
        raw = base_size * stock_mult * vix_mult
        final = int(raw)

        return {
            "symbol": symbol,
            "base_size": base_size,
            "stock_regime": stock_regime,
            "stock_regime_mult": stock_mult,
            "vix": round(vix, 2),
            "vix_regime": vix_regime,
            "vix_mult": vix_mult,
            "raw_size": round(raw, 2),
            "final_size": final,
        }
