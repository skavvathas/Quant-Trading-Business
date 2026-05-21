"""
data_fetcher.py — Download and cache 5-min and daily bars for QQQ / TQQQ.

Data is stored as Parquet files:
    data/bars/orb_qqq/5min/{symbol}.parquet
    data/bars/orb_qqq/1day/{symbol}.parquet

Run once before backtesting:
    from strategies.orb_qqq.data_fetcher import fetch_bars
    fetch_bars(["QQQ", "TQQQ"], "2016-01-01", "2023-02-17")

Alpaca free tier covers equities back to 2016.
If API keys are not set, raises an informative error.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytz

import config
from . import orb_qqq_config as cfg

log = logging.getLogger(__name__)
ET  = pytz.timezone("America/New_York")


# ── Alpaca client ──────────────────────────────────────────────────────────────

def _data_client():
    from alpaca.data.historical import StockHistoricalDataClient
    if not config.ALPACA_API_KEY:
        raise RuntimeError(
            "ALPACA_API_KEY is not set. Add it to your .env file.\n"
            "Free keys available at https://alpaca.markets/"
        )
    return StockHistoricalDataClient(
        api_key=config.ALPACA_API_KEY,
        secret_key=config.ALPACA_SECRET_KEY,
    )


# ── Internal helpers ───────────────────────────────────────────────────────────

def _store(symbol: str, timeframe: str, df: pd.DataFrame) -> None:
    """Merge new bars into existing Parquet (create if absent)."""
    path = cfg.bars_path(symbol, timeframe)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        try:
            existing = pd.read_parquet(path)
            df = pd.concat([existing, df])
        except Exception:
            pass

    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.to_parquet(path, compression="snappy")
    log.info("Saved %s %s → %d bars (%s → %s)",
             symbol, timeframe, len(df),
             df.index.min().date(), df.index.max().date())


def _is_cached(symbol: str, timeframe: str, start: str, end: str) -> bool:
    path = cfg.bars_path(symbol, timeframe)
    if not path.exists():
        return False
    try:
        idx = pd.read_parquet(path, columns=[]).index
        return str(idx.min().date()) <= start and str(idx.max().date()) >= end
    except Exception:
        return False


# ── Public API ─────────────────────────────────────────────────────────────────

def fetch_bars(
    symbols: list[str] | None = None,
    start:   str = "2016-01-01",
    end:     str = "2023-02-17",
    force:   bool = False,
) -> None:
    """
    Download 5-min and daily bars from Alpaca and cache as Parquet.

    Parameters
    ----------
    symbols : list of ticker symbols (default: QQQ + TQQQ)
    start   : ISO date string, inclusive
    end     : ISO date string, inclusive
    force   : re-download even if already cached
    """
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    symbols  = symbols or cfg.INSTRUMENTS
    client   = _data_client()
    start_dt = datetime.fromisoformat(start)
    end_dt   = datetime.fromisoformat(end)

    timeframes = [
        ("1day", TimeFrame.Day),
        ("5min", TimeFrame(5, TimeFrameUnit.Minute)),
    ]

    for tf_label, tf_obj in timeframes:
        for sym in symbols:
            if not force and _is_cached(sym, tf_label, start, end):
                log.info("Already cached: %s %s", sym, tf_label)
                continue

            log.info("Fetching %s %s bars (%s → %s)…", sym, tf_label, start, end)
            try:
                req = StockBarsRequest(
                    symbol_or_symbols=sym,
                    timeframe=tf_obj,
                    start=start_dt,
                    end=end_dt,
                )
                raw = client.get_stock_bars(req).df

                if raw.empty:
                    log.warning("No data returned for %s %s", sym, tf_label)
                    continue

                flat = raw.reset_index()
                flat["timestamp"] = pd.to_datetime(flat["timestamp"], utc=True)
                sym_df = (
                    flat[flat["symbol"] == sym]
                    .set_index("timestamp")[["open", "high", "low", "close", "volume"]]
                    if "symbol" in flat.columns else
                    flat.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
                )
                _store(sym, tf_label, sym_df)

            except Exception as e:
                log.error("Failed to fetch %s %s: %s", sym, tf_label, e)


def load_5min(symbol: str) -> pd.DataFrame:
    """Load cached 5-min bars. Raises FileNotFoundError if not yet fetched."""
    path = cfg.bars_path(symbol, "5min")
    if not path.exists():
        raise FileNotFoundError(
            f"No 5-min bars cached for {symbol}. "
            f"Run fetch_bars(['{symbol}'], start, end) first."
        )
    return pd.read_parquet(path)


def load_daily(symbol: str) -> pd.DataFrame:
    """Load cached daily bars. Raises FileNotFoundError if not yet fetched."""
    path = cfg.bars_path(symbol, "1day")
    if not path.exists():
        raise FileNotFoundError(
            f"No daily bars cached for {symbol}. "
            f"Run fetch_bars(['{symbol}'], start, end) first."
        )
    return pd.read_parquet(path)
