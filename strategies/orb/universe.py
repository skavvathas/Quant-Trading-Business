"""
universe.py — Overnight universe construction for the ORB strategy.

Run pre-market to build a watchlist of stocks passing the base filters.
Saves to data/orb_watchlist.json, which orb_scanner.py loads at 9:35 AM.

Pipeline:
  1. Fetch all active tradeable US equities from Alpaca  (~7,000 symbols)
  2. Batch daily bars → apply price / volume / ATR filters (~500–800 survivors)
  3. Batch 5-min bars for survivors → compute avg opening-range volume
  4. Save watchlist JSON
"""

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, time as dtime

import numpy as np
import pandas as pd
import pytz

import config
from strategies.orb import orb_config

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")


@dataclass
class WatchlistEntry:
    ticker: str
    atr_14d: float
    avg_14d_volume: float
    avg_14d_orvolume: float   # avg first-5-min-bar volume over last 14 trading days


# ── Alpaca clients ─────────────────────────────────────────────────────────────

def _data_client():
    from alpaca.data.historical import StockHistoricalDataClient
    return StockHistoricalDataClient(
        api_key=config.ALPACA_API_KEY or None,
        secret_key=config.ALPACA_SECRET_KEY or None,
    )


def _trading_client():
    from alpaca.trading.client import TradingClient
    return TradingClient(
        api_key=config.ALPACA_API_KEY,
        secret_key=config.ALPACA_SECRET_KEY,
        paper=config.PAPER_TRADING,
    )


# ── Step 1: all tradeable symbols ─────────────────────────────────────────────

def fetch_all_symbols() -> list[str]:
    """Return all active, tradeable US equity symbols from Alpaca."""
    from alpaca.trading.requests import GetAssetsRequest
    from alpaca.trading.enums import AssetClass, AssetStatus

    assets = _trading_client().get_all_assets(
        GetAssetsRequest(asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE)
    )
    symbols = [a.symbol for a in assets if a.tradable]
    logger.info("Alpaca universe: %d tradeable US equities", len(symbols))
    return symbols


# ── Step 2: batch daily bars ───────────────────────────────────────────────────

def fetch_daily_bars_batch(symbols: list[str]) -> pd.DataFrame:
    """
    Batch-fetch daily OHLCV for all symbols over the last LOOKBACK_DAYS + buffer.
    Returns a flat DataFrame with a 'symbol' column.
    """
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = _data_client()
    end   = datetime.now(tz=ET).date()
    start = end - timedelta(days=orb_config.LOOKBACK_DAYS + 10)

    all_dfs = []
    for i in range(0, len(symbols), orb_config.CHUNK_SIZE):
        chunk = symbols[i : i + orb_config.CHUNK_SIZE]
        try:
            req = StockBarsRequest(
                symbol_or_symbols=chunk,
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
            )
            df = client.get_stock_bars(req).df
            if not df.empty:
                all_dfs.append(df.reset_index())   # flattens MultiIndex → symbol + timestamp cols
        except Exception as e:
            logger.warning("Daily bars batch %d failed: %s", i // orb_config.CHUNK_SIZE, e)

    if not all_dfs:
        return pd.DataFrame()

    combined = pd.concat(all_dfs, ignore_index=True)
    combined["timestamp"] = pd.to_datetime(combined["timestamp"], utc=True)
    return combined


# ── Step 3: ATR computation + base filters ─────────────────────────────────────

def _atr(df: pd.DataFrame) -> float:
    """14-day ATR for a single symbol's daily OHLCV DataFrame."""
    df = df.sort_values("timestamp").tail(orb_config.LOOKBACK_DAYS + 1)
    if len(df) < 2:
        return 0.0
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return float(tr.tail(orb_config.LOOKBACK_DAYS).mean())


def apply_base_filters(daily_df: pd.DataFrame) -> dict[str, WatchlistEntry]:
    """
    For each symbol compute ATR and avg volume, keep only survivors.
    Returns {ticker: WatchlistEntry} with avg_14d_orvolume = 0 (filled in step 4).
    """
    results: dict[str, WatchlistEntry] = {}

    for sym, group in daily_df.groupby("symbol"):
        try:
            g = group.tail(orb_config.LOOKBACK_DAYS + 1)
            if len(g) < 3:
                continue
            last_close  = float(g["close"].iloc[-1])
            avg_volume  = float(g["volume"].tail(orb_config.LOOKBACK_DAYS).mean())
            atr         = _atr(g)

            if (
                last_close  > orb_config.MIN_PRICE
                and avg_volume >= orb_config.MIN_AVG_VOLUME
                and atr        > orb_config.MIN_ATR
            ):
                results[sym] = WatchlistEntry(
                    ticker=sym,
                    atr_14d=round(atr, 4),
                    avg_14d_volume=round(avg_volume, 0),
                    avg_14d_orvolume=0.0,
                )
        except Exception as e:
            logger.debug("Filter error %s: %s", sym, e)

    logger.info(
        "Base filters: %d/%d symbols pass",
        len(results), daily_df["symbol"].nunique(),
    )
    return results


# ── Step 4: avg opening-range volume (9:30–9:35 AM bar) ───────────────────────

def fetch_avg_orvolume_batch(symbols: list[str]) -> dict[str, float]:
    """
    For each symbol compute the 14-day average of the 9:30 AM 5-min bar volume.
    Returns {ticker: avg_orvolume}.
    """
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    client = _data_client()
    end   = datetime.now(tz=ET).date()
    start = end - timedelta(days=orb_config.LOOKBACK_DAYS + 10)

    avg_volumes: dict[str, float] = {}

    for i in range(0, len(symbols), orb_config.CHUNK_SIZE):
        chunk = symbols[i : i + orb_config.CHUNK_SIZE]
        try:
            req = StockBarsRequest(
                symbol_or_symbols=chunk,
                timeframe=TimeFrame(5, TimeFrameUnit.Minute),
                start=start,
                end=end,
            )
            df = client.get_stock_bars(req).df
            if df.empty:
                continue

            flat = df.reset_index()
            flat["timestamp"] = pd.to_datetime(flat["timestamp"], utc=True).dt.tz_convert(ET)

            # Keep only the 9:30 AM bar each day
            opening = flat[flat["timestamp"].dt.time == dtime(9, 30)]

            for sym in chunk:
                rows = opening[opening["symbol"] == sym]
                if not rows.empty:
                    avg_volumes[sym] = float(
                        rows["volume"].tail(orb_config.LOOKBACK_DAYS).mean()
                    )
        except Exception as e:
            logger.warning("ORVolume batch %d failed: %s", i // orb_config.CHUNK_SIZE, e)

    return avg_volumes


# ── Main overnight pipeline ────────────────────────────────────────────────────

def build_watchlist() -> list[WatchlistEntry]:
    """
    Full overnight pipeline. Saves result to data/orb_watchlist.json.
    Returns the final list of WatchlistEntry objects.
    """
    symbols = fetch_all_symbols()

    logger.info("Fetching daily bars for %d symbols...", len(symbols))
    daily_df = fetch_daily_bars_batch(symbols)
    if daily_df.empty:
        logger.error("No daily bars returned — aborting")
        return []

    watchlist = apply_base_filters(daily_df)
    if not watchlist:
        logger.error("No symbols passed base filters")
        return []

    filtered = list(watchlist.keys())
    logger.info("Fetching 5-min OR bars for %d symbols...", len(filtered))
    avg_orvols = fetch_avg_orvolume_batch(filtered)

    final: list[WatchlistEntry] = []
    for ticker, entry in watchlist.items():
        avg_orv = avg_orvols.get(ticker, 0.0)
        if avg_orv > 0:
            entry.avg_14d_orvolume = round(avg_orv, 0)
            final.append(entry)

    save_watchlist(final)
    logger.info("Watchlist built: %d symbols saved", len(final))
    return final


# ── Persistence ────────────────────────────────────────────────────────────────

def save_watchlist(entries: list[WatchlistEntry]) -> None:
    data = {
        "built_at": datetime.now(tz=ET).isoformat(),
        "count": len(entries),
        "entries": [asdict(e) for e in entries],
    }
    orb_config.watchlist_path().write_text(json.dumps(data, indent=2))


def load_watchlist() -> list[WatchlistEntry]:
    path = orb_config.watchlist_path()
    if not path.exists():
        logger.warning("Watchlist not found at %s — run build_watchlist() pre-market", path)
        return []
    data = json.loads(path.read_text())
    return [WatchlistEntry(**e) for e in data["entries"]]
