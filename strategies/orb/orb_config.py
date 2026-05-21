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

# ── Dynamic stop-loss & take-profit tiers (keyed by ATR magnitude) ────────────
# Tiers checked top-to-bottom; first match wins.
# stop_value: fixed dollar amount when stop_is_fixed=True, else ATR multiplier.
# tp_r: take-profit distance as a multiple of the stop distance (R-multiple).
ATR_TIERS: list[dict] = [
    {"atr_min": 10.0, "stop_value": 0.75, "stop_is_fixed": True,  "tp_r": 4.0},  # e.g. TSLA, NVDA
    {"atr_min":  5.0, "stop_value": 0.50, "stop_is_fixed": False, "tp_r": 4.0},  # e.g. AAPL, MSFT
    {"atr_min":  2.0, "stop_value": 0.75, "stop_is_fixed": False, "tp_r": 3.5},  # mid-range
    {"atr_min":  0.0, "stop_value": 1.00, "stop_is_fixed": False, "tp_r": 3.0},  # e.g. IMVT (ATR 1.37)
]

ATR_STOP_PCT     = 0.10        # legacy — kept for backtest compatibility only

# ── Position sizing ────────────────────────────────────────────────────────────
RISK_PER_TRADE   = 0.015       # risk 1.5% of initial capital per trade if stop is hit
MAX_LEVERAGE     = 4.0         # FINRA day-trading maximum

# ── Portfolio-level daily risk limits ─────────────────────────────────────────
MAX_DAILY_LOSS_PCT = 0.025     # stop trading if portfolio is down 2.5% on the day
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
