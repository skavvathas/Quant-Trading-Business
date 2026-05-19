"""
scanner.py — Morning scan at 9:35 AM ET for ORB Stocks in Play.

Call run_scan() immediately after the first 5-min candle closes.
It fetches today's opening candle for every watchlist symbol, computes
Relative Volume, generates ORB signals, and returns the top N by RelVol.
"""

import logging
from datetime import datetime, time as dtime

import pandas as pd
import pytz

import config
from strategies.orb import orb_config
from strategies.orb.strategy import ORBSetup, ORBSignal, generate_signal, select_top_n
from strategies.orb.universe import WatchlistEntry, load_watchlist, _data_client

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")


# ── Fetch today's 9:30–9:35 candle for all watchlist symbols ──────────────────

def fetch_opening_candles(symbols: list[str]) -> dict[str, dict]:
    """
    Batch-fetch the 9:30 AM ET 5-min bar for all symbols.
    Returns {ticker: {open, high, low, close, volume}}.
    """
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    client = _data_client()
    now   = datetime.now(tz=ET)
    start = now.replace(hour=9, minute=29, second=0, microsecond=0)

    candles: dict[str, dict] = {}

    for i in range(0, len(symbols), orb_config.CHUNK_SIZE):
        chunk = symbols[i : i + orb_config.CHUNK_SIZE]
        try:
            req = StockBarsRequest(
                symbol_or_symbols=chunk,
                timeframe=TimeFrame(5, TimeFrameUnit.Minute),
                start=start,
                end=now,
            )
            df = client.get_stock_bars(req).df
            if df.empty:
                continue

            flat = df.reset_index()
            flat["timestamp"] = pd.to_datetime(flat["timestamp"], utc=True).dt.tz_convert(ET)
            opening = flat[flat["timestamp"].dt.time == dtime(9, 30)]

            for sym in chunk:
                rows = opening[opening["symbol"] == sym]
                if not rows.empty:
                    row = rows.iloc[0]
                    candles[sym] = {
                        "open":   float(row["open"]),
                        "high":   float(row["high"]),
                        "low":    float(row["low"]),
                        "close":  float(row["close"]),
                        "volume": float(row["volume"]),
                    }
        except Exception as e:
            logger.warning("Opening candles batch %d failed: %s", i // orb_config.CHUNK_SIZE, e)

    logger.info("Opening candles: %d/%d symbols", len(candles), len(symbols))
    return candles


# ── Build ORBSetup objects ─────────────────────────────────────────────────────

def build_setups(
    watchlist: list[WatchlistEntry],
    opening_candles: dict[str, dict],
) -> list[ORBSetup]:
    """Merge watchlist metadata with today's opening candle into ORBSetup objects."""
    setups = []
    for entry in watchlist:
        candle = opening_candles.get(entry.ticker)
        if not candle:
            continue
        setups.append(ORBSetup(
            ticker=entry.ticker,
            open_price=candle["open"],
            first_candle_open=candle["open"],
            first_candle_close=candle["close"],
            first_candle_high=candle["high"],
            first_candle_low=candle["low"],
            first_candle_volume=candle["volume"],
            avg_14d_volume=entry.avg_14d_volume,
            avg_14d_orvolume=entry.avg_14d_orvolume,
            atr_14d=entry.atr_14d,
        ))
    return setups


# ── Full morning pipeline ──────────────────────────────────────────────────────

def run_scan(
    watchlist: list[WatchlistEntry] | None = None,
    top_n: int = orb_config.TOP_N,
) -> list[ORBSignal]:
    """
    Call at 9:35 AM ET after the first candle closes.
    Loads watchlist from disk if not provided, fetches opening candles,
    generates signals, and returns the top N by Relative Volume.
    """
    if watchlist is None:
        watchlist = load_watchlist()
    if not watchlist:
        logger.warning("Watchlist is empty — run universe.build_watchlist() pre-market")
        return []

    symbols = [e.ticker for e in watchlist]
    opening_candles = fetch_opening_candles(symbols)
    setups = build_setups(watchlist, opening_candles)

    signals: list[ORBSignal] = []
    for setup in setups:
        sig = generate_signal(setup, min_relvol=orb_config.MIN_RELVOL)
        if sig:
            signals.append(sig)

    top = select_top_n(signals, n=top_n)
    logger.info(
        "ORB scan complete: %d setups → %d signals → top %d by RelVol",
        len(setups), len(signals), len(top),
    )
    return top
