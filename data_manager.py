"""
data_manager.py — Market data fetching, caching, and cleaning.

Handles:
  - 5-min intraday bars from Alpaca
  - Daily bars from Alpaca
  - VIX from Yahoo Finance
  - SQLite cache with freshness checks
"""

import logging
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf
import pytz

import config

logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")

# ── SQLite helpers ─────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_cache_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bars_cache (
            symbol      TEXT    NOT NULL,
            timeframe   TEXT    NOT NULL,
            timestamp   TEXT    NOT NULL,
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      REAL,
            fetched_at  TEXT    NOT NULL,
            PRIMARY KEY (symbol, timeframe, timestamp)
        )
    """)
    conn.commit()


# ── Data cleaning ──────────────────────────────────────────────────────────────

def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """Remove NaNs, duplicates, and extreme outliers (±5 sigma on close)."""
    if df.empty:
        return df

    df = df.copy()
    df = df[~df.index.duplicated(keep="last")]
    df.sort_index(inplace=True)
    df.dropna(subset=["close"], inplace=True)

    # Winsorise close outliers using rolling 20-bar window
    if len(df) >= 20:
        roll_mean = df["close"].rolling(20, min_periods=5).mean()
        roll_std = df["close"].rolling(20, min_periods=5).std()
        lower = roll_mean - 5 * roll_std
        upper = roll_mean + 5 * roll_std
        mask = (df["close"] >= lower) & (df["close"] <= upper)
        removed = (~mask).sum()
        if removed:
            logger.debug("clean_data: removed %d outlier rows for", removed)
        df = df[mask]

    return df


# ── Cache read/write ───────────────────────────────────────────────────────────

def cache_bars_local(symbol: str, bars: pd.DataFrame, timeframe: str) -> None:
    """Persist bars DataFrame to SQLite cache."""
    if bars.empty:
        return
    now_str = datetime.utcnow().isoformat()
    with _get_conn() as conn:
        _ensure_cache_table(conn)
        records = []
        for ts, row in bars.iterrows():
            ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            records.append((
                symbol, timeframe, ts_str,
                float(row.get("open", 0)), float(row.get("high", 0)),
                float(row.get("low", 0)),  float(row.get("close", 0)),
                float(row.get("volume", 0)), now_str
            ))
        conn.executemany("""
            INSERT OR REPLACE INTO bars_cache
            (symbol, timeframe, timestamp, open, high, low, close, volume, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, records)
        conn.commit()
    logger.debug("Cached %d bars for %s/%s", len(records), symbol, timeframe)


def get_cached_bars(symbol: str, timeframe: str) -> pd.DataFrame:
    """Return cached bars as DataFrame (empty if cache miss)."""
    try:
        with _get_conn() as conn:
            _ensure_cache_table(conn)
            df = pd.read_sql_query(
                "SELECT * FROM bars_cache WHERE symbol=? AND timeframe=? ORDER BY timestamp",
                conn, params=(symbol, timeframe)
            )
        if df.empty:
            return pd.DataFrame()
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df.set_index("timestamp", inplace=True)
        return df[["open", "high", "low", "close", "volume"]]
    except Exception as e:
        logger.warning("Cache read error for %s/%s: %s", symbol, timeframe, e)
        return pd.DataFrame()


def is_cache_fresh(symbol: str, timeframe: str,
                   max_age_minutes: int = config.CACHE_MAX_AGE_MINUTES) -> bool:
    """Return True if the most recent cache entry is within max_age_minutes."""
    try:
        with _get_conn() as conn:
            _ensure_cache_table(conn)
            row = conn.execute(
                "SELECT MAX(fetched_at) FROM bars_cache WHERE symbol=? AND timeframe=?",
                (symbol, timeframe)
            ).fetchone()
        if not row or not row[0]:
            return False
        fetched_at = datetime.fromisoformat(row[0])
        age = (datetime.utcnow() - fetched_at).total_seconds() / 60
        return age <= max_age_minutes
    except Exception:
        return False


# ── Alpaca fetch helpers ───────────────────────────────────────────────────────

def _alpaca_client():
    """Lazy-import alpaca-py and return a StockHistoricalDataClient."""
    from alpaca.data.historical import StockHistoricalDataClient
    return StockHistoricalDataClient(
        api_key=config.ALPACA_API_KEY,
        secret_key=config.ALPACA_SECRET_KEY,
    )


def _alpaca_bars_to_df(bars) -> pd.DataFrame:
    """Convert alpaca-py Bar objects to a tidy DataFrame."""
    records = []
    for bar in bars:
        records.append({
            "timestamp": bar.timestamp,
            "open": float(bar.open),
            "high": float(bar.high),
            "low":  float(bar.low),
            "close": float(bar.close),
            "volume": float(bar.volume),
        })
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df.set_index("timestamp", inplace=True)
    return df


def fetch_5min_bars(symbol: str, limit: int = config.INTRADAY_BAR_LIMIT,
                    use_cache: bool = True) -> pd.DataFrame:
    """
    Fetch 5-minute OHLCV bars for symbol.

    Uses local cache when fresh to avoid Alpaca rate limits.
    Falls back to yfinance if Alpaca credentials are absent.
    """
    if use_cache and is_cache_fresh(symbol, "5min"):
        logger.debug("Cache hit for %s/5min", symbol)
        return get_cached_bars(symbol, "5min")

    if not config.ALPACA_API_KEY:
        return _yf_intraday_bars(symbol, limit)

    try:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        client = _alpaca_client()
        end = datetime.now(tz=ET)
        start = end - timedelta(days=5)   # enough history to cover market hours
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute * 5,
            start=start,
            end=end,
            limit=limit,
        )
        raw = client.get_stock_bars(req)
        bars_list = raw[symbol] if isinstance(raw, dict) else raw.data.get(symbol, [])
        df = _alpaca_bars_to_df(bars_list)
        df = clean_data(df)
        if not df.empty:
            cache_bars_local(symbol, df, "5min")
        return df
    except Exception as e:
        logger.error("Alpaca 5min fetch failed for %s: %s — falling back to yfinance", symbol, e)
        return _yf_intraday_bars(symbol, limit)


def _flatten_yf(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Normalise yfinance MultiIndex or flat columns to lowercase flat."""
    if isinstance(df.columns, pd.MultiIndex):
        try:
            df = df.xs(symbol, level="Ticker", axis=1)
        except KeyError:
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df.columns = [c.lower() for c in df.columns]
    return df


def _yf_intraday_bars(symbol: str, limit: int = 100) -> pd.DataFrame:
    """yfinance fallback for 5-min bars (last 7 days)."""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="5d", interval="5m", auto_adjust=True)
        if df.empty:
            return pd.DataFrame()
        df.index = pd.to_datetime(df.index, utc=True)
        df.columns = [c.lower() for c in df.columns]
        df = df[["open", "high", "low", "close", "volume"]].tail(limit)
        df = clean_data(df)
        cache_bars_local(symbol, df, "5min")
        return df
    except Exception as e:
        logger.error("yfinance 5min fetch failed for %s: %s", symbol, e)
        return pd.DataFrame()


def fetch_daily_bars(symbol: str, limit: int = config.DAILY_BAR_LIMIT,
                     use_cache: bool = True) -> pd.DataFrame:
    """
    Fetch daily OHLCV bars for realized volatility calculation.

    Cache TTL is longer for daily bars (no need for sub-minute freshness).
    """
    if use_cache and is_cache_fresh(symbol, "1day", max_age_minutes=60):
        logger.debug("Cache hit for %s/1day", symbol)
        return get_cached_bars(symbol, "1day")

    if not config.ALPACA_API_KEY:
        return _yf_daily_bars(symbol, limit)

    try:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        client = _alpaca_client()
        end = datetime.now(tz=ET)
        start = end - timedelta(days=limit * 2)  # buffer for non-trading days
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            limit=limit,
        )
        raw = client.get_stock_bars(req)
        bars_list = raw[symbol] if isinstance(raw, dict) else raw.data.get(symbol, [])
        df = _alpaca_bars_to_df(bars_list)
        df = clean_data(df)
        if not df.empty:
            cache_bars_local(symbol, df, "1day")
        return df
    except Exception as e:
        logger.error("Alpaca daily fetch failed for %s: %s — falling back to yfinance", symbol, e)
        return _yf_daily_bars(symbol, limit)


def _yf_daily_bars(symbol: str, limit: int = 60) -> pd.DataFrame:
    """yfinance fallback for daily bars."""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=f"{limit * 2}d", interval="1d", auto_adjust=True)
        if df.empty:
            return pd.DataFrame()
        df.index = pd.to_datetime(df.index, utc=True)
        df.columns = [c.lower() for c in df.columns]
        wanted = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[wanted].tail(limit)
        df = clean_data(df)
        cache_bars_local(symbol, df, "1day")
        return df
    except Exception as e:
        logger.error("yfinance daily fetch failed for %s: %s", symbol, e)
        return pd.DataFrame()


# ── VIX fetch ──────────────────────────────────────────────────────────────────

_vix_cache: dict = {"value": None, "fetched_at": None}
_VIX_CACHE_TTL_MINUTES = 15


def get_vix() -> float:
    """
    Fetch current VIX level from Yahoo Finance (^VIX).

    Caches in-process for 15 minutes.  Returns 20.0 as a safe default if
    the fetch fails (maps to 'medium_vol' market regime, 0.8× sizing).
    """
    now = datetime.utcnow()
    if (
        _vix_cache["value"] is not None
        and _vix_cache["fetched_at"] is not None
        and (now - _vix_cache["fetched_at"]).total_seconds() < _VIX_CACHE_TTL_MINUTES * 60
    ):
        return _vix_cache["value"]

    try:
        ticker = yf.Ticker("^VIX")
        hist = ticker.history(period="2d", interval="1d")
        if hist.empty:
            raise ValueError("Empty VIX data")
        vix_value = float(hist["Close"].iloc[-1])
        _vix_cache["value"] = vix_value
        _vix_cache["fetched_at"] = now
        logger.info("VIX fetched: %.2f", vix_value)
        return vix_value
    except Exception as e:
        logger.warning("VIX fetch failed: %s — using default 20.0", e)
        return 20.0


# ── Convenience: fetch historical VIX series ──────────────────────────────────

def fetch_vix_history(start: str, end: str) -> pd.Series:
    """Return a daily VIX close series for the given date range (for backtest)."""
    try:
        df = yf.download("^VIX", start=start, end=end, interval="1d",
                         auto_adjust=True, progress=False)
        if df.empty:
            return pd.Series(dtype=float)
        # Normalise MultiIndex columns (yfinance ≥ 0.2.38)
        if isinstance(df.columns, pd.MultiIndex):
            df = df.xs("^VIX", level="Ticker", axis=1)
        df.columns = [c.lower() for c in df.columns]
        return df["close"].squeeze()
    except Exception as e:
        logger.error("VIX history fetch failed: %s", e)
        return pd.Series(dtype=float)
