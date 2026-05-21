"""
orb_qqq_config.py — All parameters for the ORB QQQ/TQQQ strategy.
Mirrors the exact setup from Zarattini & Aziz (2023/2025).
"""

from pathlib import Path

# ── Instruments ────────────────────────────────────────────────────────────────
INSTRUMENTS: list[str] = ["QQQ", "TQQQ"]

# ── Baseline strategy (paper Section 2-3) ─────────────────────────────────────
RISK_PER_TRADE   = 0.01      # 1% of account risked per trade if stop is hit
MAX_LEVERAGE     = 4.0       # FINRA intraday maximum for retail accounts
TARGET_R         = 10.0      # profit target in R-multiples (10R)
COMMISSION       = 0.0005    # $ per share (matches paper: $0.0005)
STARTING_CAPITAL = 25_000.0  # paper starting capital

# ── Optimised variant (paper Section 4) ───────────────────────────────────────
# Stop = ATR_STOP_PCT × 14-day ATR.  No fixed profit target — hold until EOD.
ATR_PERIOD       = 14        # trading days for ATR calculation
ATR_STOP_PCT     = 0.05      # 5% of 14-day ATR — paper default (2016-2023)
                             # Use 10% for out-of-sample 2024-2026 (better OOS Sharpe)

# ── Session window (ET) ────────────────────────────────────────────────────────
MARKET_OPEN_ET   = "09:30"   # first bar opens here
CANDLE_1_CLOSE   = "09:35"   # end of 1st 5-min candle
CANDLE_2_OPEN    = "09:35"   # entry: open of 2nd 5-min candle
MARKET_CLOSE_ET  = "16:00"   # EOD exit (last bar close)

# ── Data storage ───────────────────────────────────────────────────────────────
def bars_dir() -> Path:
    import config
    return config.DATA_DIR / "bars" / "orb_qqq"

def bars_path(symbol: str, timeframe: str) -> Path:
    """timeframe: '5min' | '1day'"""
    return bars_dir() / timeframe / f"{symbol}.parquet"

# ── Output paths ───────────────────────────────────────────────────────────────
def backtest_trades_path(instrument: str, variant: str = "baseline", tag: str = "") -> Path:
    import config
    suffix = f"_{tag}" if tag else ""
    return config.OUTPUTS_DIR / f"orbqqq_{instrument.lower()}_{variant}{suffix}_trades.csv"

def backtest_report_path(instrument: str, variant: str = "baseline", tag: str = "") -> Path:
    import config
    suffix = f"_{tag}" if tag else ""
    return config.OUTPUTS_DIR / f"orbqqq_{instrument.lower()}_{variant}{suffix}_report.csv"
