"""
adaptive_trend_config.py — All parameters for the AdaptiveTrend strategy.
Based on: "Systematic Trend-Following in Crypto Markets" (2025).
Data source: Alpaca Crypto API (spot, USD pairs).
"""

from pathlib import Path

# ── Signal parameters ──────────────────────────────────────────────────────────
LOOKBACK_BARS   = 20            # L — momentum lookback (20 × 6h ≈ 5 days)
ENTRY_THRESHOLD = 0.02          # θ_entry — min MOM to enter long (2%)
SHORT_THRESHOLD = 0.02          # θ_entry_short — min |MOM| to enter short (2%)
BAR_INTERVAL_H  = 6             # bar width in hours (6h OHLCV)

# ── Trailing stop ──────────────────────────────────────────────────────────────
ALPHA           = 2.5           # α — ATR multiplier for trailing stop
ATR_PERIOD      = 14            # ATR calculation window (bars)

# ── Portfolio construction ─────────────────────────────────────────────────────
LAMBDA_LONG     = 0.70          # fraction of capital allocated to long leg
LAMBDA_SHORT    = 0.30          # fraction of capital allocated to short leg
K_LONG          = 10            # top-N by 30-day dollar volume for long universe
K_SHORT         = 5             # bottom-N by momentum for short universe

# ── Sharpe ratio filters ───────────────────────────────────────────────────────
SHARPE_LOOKBACK_BARS = 360      # trailing window (360 × 6h = 90 days)
GAMMA_LONG      = 1.3           # min trailing Sharpe to enter long
GAMMA_SHORT     = 1.7           # min trailing Sharpe to enter short

# ── Rebalancing ────────────────────────────────────────────────────────────────
REBALANCE_FREQ  = "monthly"     # portfolio rebalanced at start of each month

# ── Alpaca crypto universe — USD spot pairs ────────────────────────────────────
# Filtered to liquid names available on Alpaca spot market.
UNIVERSE: list[str] = [
    "BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "DOT/USD",
    "LINK/USD", "UNI/USD", "BCH/USD", "LTC/USD", "AAVE/USD",
    "BAT/USD", "GRT/USD", "FIL/USD", "ARB/USD", "LDO/USD",
    "CRV/USD", "SUSHI/USD", "YFI/USD", "XTZ/USD", "POL/USD",
]

# ── Data fetching ──────────────────────────────────────────────────────────────
DATA_LIMIT      = 500           # max bars to fetch per request (Alpaca max ~1000)
FETCH_DAYS      = 120           # how many calendar days of history to pull

# ── Transaction costs (spot, no leverage) ─────────────────────────────────────
TAKER_FEE_BPS   = 25            # Alpaca crypto taker fee ~0.25%
SLIPPAGE_BPS    = 5             # estimated slippage per side

# ── Persistence ────────────────────────────────────────────────────────────────
def scan_result_path() -> Path:
    import config
    return config.DATA_DIR / "adaptive_trend_scan.json"

def bars_dir() -> Path:
    import config
    return config.DATA_DIR / "bars" / "crypto_6h"

def backtest_trades_path() -> Path:
    import config
    return config.OUTPUTS_DIR / "at_backtest_trades.csv"

def backtest_report_path() -> Path:
    import config
    return config.OUTPUTS_DIR / "at_backtest_report.csv"
