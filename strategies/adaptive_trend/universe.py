"""
universe.py — Fetch crypto OHLCV bars via Alpaca and rank by dollar volume.
Alpaca spot crypto (USD pairs) replaces ccxt/Binance futures.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from . import adaptive_trend_config as cfg

log = logging.getLogger(__name__)


# ── Alpaca data client ─────────────────────────────────────────────────────────

def _data_client():
    from alpaca.data.historical import CryptoHistoricalDataClient
    import config
    return CryptoHistoricalDataClient(
        api_key=config.ALPACA_API_KEY or None,
        secret_key=config.ALPACA_SECRET_KEY or None,
    )


# ── Bar fetching ───────────────────────────────────────────────────────────────

def fetch_ohlcv(
    symbol: str,
    start:  Optional[datetime] = None,
    end:    Optional[datetime] = None,
) -> Optional[pd.DataFrame]:
    """
    Fetch 6-hour OHLCV bars for one symbol from Alpaca.
    Returns DataFrame with columns [open, high, low, close, volume] or None on failure.
    """
    from alpaca.data.requests import CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    if end is None:
        end = datetime.now(tz=timezone.utc)
    if start is None:
        start = end - timedelta(days=cfg.FETCH_DAYS)

    try:
        client = _data_client()
        req = CryptoBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame(cfg.BAR_INTERVAL_H, TimeFrameUnit.Hour),
            start=start,
            end=end,
        )
        bars = client.get_crypto_bars(req)
        df = bars.df

        if df.empty:
            return None

        # Drop multi-index if present (symbol + timestamp)
        if isinstance(df.index, pd.MultiIndex):
            df = df.droplevel(0)

        df.index = pd.to_datetime(df.index, utc=True)
        df = df[["open", "high", "low", "close", "volume"]].sort_index()
        return df

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
    min_bars = cfg.LOOKBACK_BARS + cfg.ATR_PERIOD + 5

    for sym in symbols:
        df = fetch_ohlcv(sym, start=start, end=end)
        if df is not None and len(df) >= min_bars:
            bars[sym] = df
        else:
            log.debug("Skipping %s — insufficient bars (%s)", sym, len(df) if df is not None else 0)

    log.info("Fetched OHLCV for %d / %d symbols", len(bars), len(symbols))
    return bars


def save_bars(bars: dict[str, pd.DataFrame]) -> None:
    """Persist bars to Parquet for backtest reuse."""
    out_dir = cfg.bars_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    for sym, df in bars.items():
        safe = sym.replace("/", "_")
        df.to_parquet(out_dir / f"{safe}.parquet")
    log.info("Saved %d bar files to %s", len(bars), out_dir)


def load_bars(symbols: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Load cached Parquet bars from disk."""
    out_dir = cfg.bars_dir()
    bars: dict[str, pd.DataFrame] = {}
    symbols = symbols or cfg.UNIVERSE
    for sym in symbols:
        safe = sym.replace("/", "_")
        p = out_dir / f"{safe}.parquet"
        if p.exists():
            bars[sym] = pd.read_parquet(p)
    return bars


# ── Universe ranking ───────────────────────────────────────────────────────────

def rank_by_dollar_volume(bars: dict[str, pd.DataFrame], window: int = 30) -> list[str]:
    """Rank symbols by trailing 30-bar avg dollar volume (close × volume)."""
    scores: dict[str, float] = {}
    for sym, df in bars.items():
        dv = (df["close"] * df["volume"]).tail(window).mean()
        scores[sym] = dv
    return sorted(scores, key=lambda s: scores[s], reverse=True)


def get_long_universe(bars: dict[str, pd.DataFrame], k: int = cfg.K_LONG) -> list[str]:
    """Top-K symbols by dollar volume → long leg universe."""
    return rank_by_dollar_volume(bars)[:k]


def get_short_universe(bars: dict[str, pd.DataFrame], k: int = cfg.K_SHORT) -> list[str]:
    """Bottom-K symbols by recent momentum → short leg candidates."""
    from .strategy import compute_momentum
    scores: dict[str, float] = {}
    for sym, df in bars.items():
        m = compute_momentum(df["close"])
        val = m.iloc[-1]
        if not pd.isna(val):
            scores[sym] = val
    ranked = sorted(scores, key=lambda s: scores[s])  # ascending: worst momentum first
    return ranked[:k]
