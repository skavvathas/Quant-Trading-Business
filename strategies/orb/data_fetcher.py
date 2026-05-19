"""
data_fetcher.py — Fetch and cache historical data for the ORB backtest.

Data is stored as Parquet files (one per symbol) in:
    data/bars/daily/  — daily OHLCV bars
    data/bars/5min/   — 5-minute OHLCV bars

Two steps (run once before backtesting):
  1. fetch_top_symbols(n)        — rank all Alpaca US equities by avg dollar volume
  2. download_bars(symbols, ...) — bulk-pull bars and write to Parquet
"""

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytz

import config
from strategies.orb import orb_config

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")

UNIVERSE_PATH = config.DATA_DIR / "orb_universe.json"
BARS_DIR      = config.DATA_DIR / "bars"
DAILY_DIR     = BARS_DIR / "daily"
MIN5_DIR      = BARS_DIR / "5min"


# ── Path helper (imported by backtest.py) ──────────────────────────────────────

def bars_path(symbol: str, timeframe: str) -> Path:
    """Return the Parquet file path for a given symbol and timeframe."""
    folder = DAILY_DIR if timeframe == "1day" else MIN5_DIR
    return folder / f"{symbol}.parquet"


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


# ── Step 1: top N symbols by avg dollar volume ────────────────────────────────

def fetch_top_symbols(n: int = 800) -> list[str]:
    """
    Fetch all Alpaca tradeable US equities.
    Rank by average dollar volume (price × volume) over the last 30 days.
    Return the top N symbols.
    """
    from alpaca.trading.requests import GetAssetsRequest
    from alpaca.trading.enums import AssetClass, AssetStatus
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    assets = _trading_client().get_all_assets(
        GetAssetsRequest(asset_class=AssetClass.US_EQUITY, status=AssetStatus.ACTIVE)
    )
    all_symbols = [a.symbol for a in assets if a.tradable]
    logger.info("Total tradeable symbols from Alpaca: %d", len(all_symbols))

    end    = datetime.now(tz=ET).date()
    start  = end - timedelta(days=40)
    client = _data_client()
    dollar_volumes: dict[str, float] = {}
    total_chunks = (len(all_symbols) - 1) // orb_config.CHUNK_SIZE + 1

    for i in range(0, len(all_symbols), orb_config.CHUNK_SIZE):
        chunk = all_symbols[i : i + orb_config.CHUNK_SIZE]
        try:
            req = StockBarsRequest(
                symbol_or_symbols=chunk,
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
            )
            df = client.get_stock_bars(req).df
            if not df.empty:
                flat = df.reset_index()
                flat["dv"] = flat["close"] * flat["volume"]
                dollar_volumes.update(flat.groupby("symbol")["dv"].mean().to_dict())
        except Exception as e:
            logger.warning("Dollar vol chunk %d/%d failed: %s", i // orb_config.CHUNK_SIZE + 1, total_chunks, e)

        time.sleep(0.3)
        if (i // orb_config.CHUNK_SIZE) % 10 == 0:
            logger.info("  Dollar volume fetch: %d%% done", min(100, int(i / len(all_symbols) * 100)))

    ranked = sorted(dollar_volumes, key=lambda s: dollar_volumes[s], reverse=True)
    top    = ranked[:n]
    logger.info("Selected top %d symbols by avg dollar volume", len(top))
    return top


def save_universe(symbols: list[str]) -> None:
    data = {"created_at": datetime.now(tz=ET).isoformat(), "count": len(symbols), "symbols": symbols}
    UNIVERSE_PATH.write_text(json.dumps(data, indent=2))
    logger.info("Universe saved: %d symbols → %s", len(symbols), UNIVERSE_PATH)


def load_universe() -> list[str]:
    if not UNIVERSE_PATH.exists():
        raise FileNotFoundError(f"Universe not found. Run fetch_top_symbols() first.")
    return json.loads(UNIVERSE_PATH.read_text())["symbols"]


# ── Parquet read / write helpers ───────────────────────────────────────────────

def _is_cached(symbol: str, timeframe: str, start: str, end: str) -> bool:
    """Return True if the Parquet file exists and fully covers [start, end]."""
    path = bars_path(symbol, timeframe)
    if not path.exists():
        return False
    try:
        idx = pd.read_parquet(path, columns=[]).index
        if idx.empty:
            return False
        return str(idx.min().date()) <= start and str(idx.max().date()) >= end
    except Exception:
        return False


def _store_bars(symbol: str, timeframe: str, new_df: pd.DataFrame) -> None:
    """
    Merge new_df into the existing Parquet file (or create it).
    new_df must have a UTC DatetimeTZDtype index named 'timestamp'.
    """
    path = bars_path(symbol, timeframe)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        try:
            existing = pd.read_parquet(path)
            combined = pd.concat([existing, new_df])
        except Exception:
            combined = new_df
    else:
        combined = new_df

    combined = combined[~combined.index.duplicated(keep="last")].sort_index()
    combined.to_parquet(path, compression="snappy")


# ── Step 2: bulk download into Parquet ────────────────────────────────────────

def download_bars(
    symbols: list[str],
    start: str,
    end: str,
    timeframe: str = "both",   # "daily" | "5min" | "both"
) -> None:
    """
    Bulk-download daily and/or 5-min bars for all symbols.
    Writes one Parquet file per symbol into data/bars/daily/ or data/bars/5min/.
    Already-cached symbols are skipped automatically.
    """
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    client   = _data_client()
    start_dt = datetime.fromisoformat(start)
    end_dt   = datetime.fromisoformat(end)

    jobs = []
    if timeframe in ("daily", "both"):
        jobs.append(("1day", TimeFrame.Day))
    if timeframe in ("5min", "both"):
        jobs.append(("5min", TimeFrame(5, TimeFrameUnit.Minute)))

    for tf_label, tf_obj in jobs:
        missing = [s for s in symbols if not _is_cached(s, tf_label, start, end)]
        total   = len(missing)
        cached  = len(symbols) - total
        logger.info(
            "Downloading %s bars: %d symbols needed  (%d already cached)",
            tf_label, total, cached,
        )

        for i in range(0, total, orb_config.CHUNK_SIZE):
            chunk = missing[i : i + orb_config.CHUNK_SIZE]
            try:
                req = StockBarsRequest(
                    symbol_or_symbols=chunk,
                    timeframe=tf_obj,
                    start=start_dt,
                    end=end_dt,
                )
                df = client.get_stock_bars(req).df
                if df.empty:
                    continue

                flat = df.reset_index()
                flat["timestamp"] = pd.to_datetime(flat["timestamp"], utc=True)

                for sym in chunk:
                    sym_df = (
                        flat[flat["symbol"] == sym]
                        .set_index("timestamp")[["open", "high", "low", "close", "volume"]]
                    )
                    if not sym_df.empty:
                        _store_bars(sym, tf_label, sym_df)

            except Exception as e:
                logger.error("%s chunk %d failed: %s", tf_label, i // orb_config.CHUNK_SIZE, e)

            time.sleep(0.3)
            done = min(i + orb_config.CHUNK_SIZE, total)
            logger.info("  %s: %d/%d  (%.0f%%)", tf_label, done, total, done / max(total, 1) * 100)

    logger.info("Download complete.  Files in: %s", BARS_DIR)


# ── Convenience: both steps in one call ───────────────────────────────────────

def prepare(start: str, end: str, n: int = 800) -> list[str]:
    """Fetch top N symbols, save universe, download all bars. Run once before backtesting."""
    symbols = fetch_top_symbols(n)
    save_universe(symbols)
    download_bars(symbols, start, end, timeframe="both")
    return symbols
