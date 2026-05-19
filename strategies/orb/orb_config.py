"""
orb_config.py — All parameters for the ORB strategy in one place.
Keeps ORB settings self-contained and separate from the main config.
"""

from pathlib import Path

# ── Universe filters (applied overnight) ──────────────────────────────────────
LOOKBACK_DAYS    = 14          # days of history for ATR + avg volume
MIN_PRICE        = 5.0         # opening price must exceed this
MIN_AVG_VOLUME   = 1_000_000   # 14-day avg daily volume
MIN_ATR          = 0.50        # 14-day ATR in dollars

# ── Stocks in Play filter (applied at 9:35 AM) ────────────────────────────────
MIN_RELVOL       = 1.0         # 100% — today's OR volume >= 14-day avg OR volume
TOP_N            = 20          # keep top N by RelVol

# ── Entry / exit ───────────────────────────────────────────────────────────────
ATR_STOP_PCT     = 0.10        # stop loss = 10% of daily ATR from entry price

# ── Position sizing ────────────────────────────────────────────────────────────
RISK_PER_TRADE   = 0.01        # risk 1% of initial capital per trade if stop is hit
MAX_LEVERAGE     = 4.0         # FINRA day-trading maximum

# ── Portfolio-level daily risk limits ─────────────────────────────────────────
MAX_DAILY_LOSS_PCT = 0.05      # stop trading if portfolio is down 5% on the day
MAX_DAILY_GAIN_PCT = 0.05      # stop trading if portfolio is up 5% on the day

# ── Data fetching ──────────────────────────────────────────────────────────────
CHUNK_SIZE       = 200         # symbols per Alpaca batch request

# ── Persistence ────────────────────────────────────────────────────────────────
# Resolved against the project data/ dir at import time via main config
def watchlist_path() -> Path:
    import config
    return config.DATA_DIR / "orb_watchlist.json"

# ── Output CSVs ────────────────────────────────────────────────────────────────
def orders_csv() -> Path:
    import config
    return config.OUTPUTS_DIR / "orb_orders.csv"

def trades_csv() -> Path:
    import config
    return config.OUTPUTS_DIR / "orb_trades.csv"

def daily_trades_log(date_str: str = "") -> Path:
    """Return path to today's per-day trade log: logs/trades/trades_YYYY-MM-DD.csv."""
    import config
    from datetime import datetime
    import pytz
    log_dir = config.LOGS_DIR / "trades"
    log_dir.mkdir(exist_ok=True)
    if not date_str:
        date_str = datetime.now(tz=pytz.timezone("America/New_York")).strftime("%Y-%m-%d")
    return log_dir / f"trades_{date_str}.csv"
