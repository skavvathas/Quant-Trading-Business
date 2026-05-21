"""
wheel_config.py — All parameters for the Wheel options income strategy.
Leg 1: Cash-Secured Put (CSP) · Leg 2: Covered Call (CC).
Data: yfinance daily OHLCV. Execution: Alpaca options API.
"""

from pathlib import Path

# ── Universe ───────────────────────────────────────────────────────────────────
UNIVERSE: list[str] = [
    "AAPL", "MSFT", "NVDA", "AMD",  "TSLA",
    "AMZN", "META", "GOOGL", "SPY", "QQQ",
    "JPM",  "BAC",  "GS",   "MS",
    "XOM",  "CVX",
]

# ── Option selection ───────────────────────────────────────────────────────────
CSP_TARGET_DELTA  = 0.25    # sell put near 25-delta (5–10% OTM)
CC_TARGET_DELTA   = 0.25    # sell call near 25-delta (above cost basis)
TARGET_DTE        = 35      # days to expiration at entry (theta sweet spot)
CLOSE_PROFIT_PCT  = 0.50    # buy back when option has lost 50% of its initial value

# ── Filters ────────────────────────────────────────────────────────────────────
MIN_IV_RANK       = 30.0    # min IV rank (0–100); only sell when IV is elevated
MIN_ANN_YIELD     = 0.20    # min annualised premium yield on capital (20%)
IV_PREMIUM        = 1.15    # implied_vol = HV30 × IV_PREMIUM (typical market IV vs. HV)

# ── Position sizing ────────────────────────────────────────────────────────────
MAX_POSITION_PCT  = 0.10    # max capital per stock (10% of equity per CSP position)
MAX_POSITIONS     = 5       # max concurrent wheel positions
CONTRACTS         = 1       # contracts per position (100 shares per contract)

# ── Costs ──────────────────────────────────────────────────────────────────────
COMMISSION_PER_CONTRACT = 0.65    # $ per contract leg
SLIPPAGE_BPS            = 10      # bid/ask half-spread assumption

# ── Data ──────────────────────────────────────────────────────────────────────
HIST_VOL_WINDOW   = 30      # trading days for HV30 (used as IV proxy in backtest)
FETCH_DAYS        = 400     # calendar days of history to pull

# ── Persistence ────────────────────────────────────────────────────────────────
def scan_result_path() -> Path:
    import config
    return config.DATA_DIR / "wheel_scan.json"

def bars_dir() -> Path:
    import config
    return config.DATA_DIR / "bars" / "wheel_equity"

def backtest_trades_path() -> Path:
    import config
    return config.OUTPUTS_DIR / "wheel_backtest_trades.csv"

def backtest_report_path() -> Path:
    import config
    return config.OUTPUTS_DIR / "wheel_backtest_report.csv"
