"""
universe.py — Fetch daily equity OHLCV via yfinance and compute IV-rank metrics.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from . import wheel_config as cfg

log = logging.getLogger(__name__)


def fetch_ohlcv(
    symbol: str,
    start:  Optional[datetime] = None,
    end:    Optional[datetime] = None,
) -> Optional[pd.DataFrame]:
    """Fetch daily OHLCV via yfinance. Returns DataFrame or None on failure."""
    try:
        import yfinance as yf
        if end is None:
            end = datetime.now()
        if start is None:
            start = end - timedelta(days=cfg.FETCH_DAYS)

        df = yf.download(
            symbol,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            auto_adjust=True,
            progress=False,
            multi_level_column=False,
        )
        if df.empty:
            return None

        df.columns = [c.lower() for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        return df[["open", "high", "low", "close", "volume"]].sort_index()
    except Exception as e:
        log.warning("fetch_ohlcv(%s): %s", symbol, e)
        return None


def fetch_all_ohlcv(
    symbols: list[str] | None = None,
    start:   Optional[datetime] = None,
    end:     Optional[datetime] = None,
) -> dict[str, pd.DataFrame]:
    """Fetch OHLCV for all universe symbols. Returns {symbol: df}."""
    symbols = symbols or cfg.UNIVERSE
    bars: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = fetch_ohlcv(sym, start=start, end=end)
        if df is not None and len(df) >= cfg.HIST_VOL_WINDOW + 30:
            bars[sym] = df
        else:
            log.debug("Skipping %s — insufficient data", sym)
    log.info("Fetched OHLCV for %d / %d symbols", len(bars), len(symbols))
    return bars


def save_bars(bars: dict[str, pd.DataFrame]) -> None:
    out_dir = cfg.bars_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    for sym, df in bars.items():
        df.to_parquet(out_dir / f"{sym}.parquet")
    log.info("Saved %d bar files to %s", len(bars), out_dir)


def load_bars(symbols: list[str] | None = None) -> dict[str, pd.DataFrame]:
    out_dir = cfg.bars_dir()
    bars: dict[str, pd.DataFrame] = {}
    for sym in (symbols or cfg.UNIVERSE):
        p = out_dir / f"{sym}.parquet"
        if p.exists():
            bars[sym] = pd.read_parquet(p)
    return bars


# ── IV metrics ─────────────────────────────────────────────────────────────────

def compute_hv(close: pd.Series, window: int = cfg.HIST_VOL_WINDOW) -> pd.Series:
    """Annualized historical volatility (close-to-close log returns)."""
    log_ret = np.log(close / close.shift(1))
    return log_ret.rolling(window).std(ddof=1) * np.sqrt(252)


def compute_iv_rank(hv_series: pd.Series, lookback: int = 252) -> float:
    """
    IV Rank = (current_hv − min_52w) / (max_52w − min_52w) × 100.
    Uses realized HV as IV proxy.
    """
    window = hv_series.dropna().tail(lookback)
    if len(window) < 20:
        return 0.0
    current = window.iloc[-1]
    lo, hi  = window.min(), window.max()
    if hi == lo:
        return 50.0
    return float((current - lo) / (hi - lo) * 100.0)


def get_stock_metrics(bars: dict[str, pd.DataFrame]) -> dict[str, dict]:
    """
    Returns per-symbol dict of:
      price, hv30, iv_rank, implied_vol (hv30 × IV_PREMIUM)
    """
    metrics: dict[str, dict] = {}
    for sym, df in bars.items():
        hv_series  = compute_hv(df["close"])
        iv_rank    = compute_iv_rank(hv_series)
        hv30       = float(hv_series.iloc[-1]) if not pd.isna(hv_series.iloc[-1]) else 0.0
        metrics[sym] = {
            "price":       round(float(df["close"].iloc[-1]), 4),
            "hv30":        round(hv30, 4),
            "iv_rank":     round(iv_rank, 1),
            "implied_vol": round(hv30 * cfg.IV_PREMIUM, 4),
        }
    return metrics
