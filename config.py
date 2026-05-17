"""
config.py — Central configuration for the Regime-Based Mean Reversion system.

All strategy parameters, API credentials, and universe settings live here.
Change thresholds here; no code elsewhere needs to change.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Project paths ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
OUTPUTS_DIR = BASE_DIR / "outputs"

for _d in (DATA_DIR, LOGS_DIR, OUTPUTS_DIR):
    _d.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "bars_cache.db"

# ── CSV output files ───────────────────────────────────────────────────────────
SIGNALS_CSV = OUTPUTS_DIR / "signals.csv"
ORDERS_CSV = OUTPUTS_DIR / "orders.csv"
TRADES_CSV = OUTPUTS_DIR / "trades.csv"

# ── Alpaca credentials ─────────────────────────────────────────────────────────
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
PAPER_TRADING = True  # flip to False for live

# ── Full tradeable universe (shown in dashboard picker) ───────────────────────
FULL_UNIVERSE: dict[str, str] = {
    "NBIS":  "Nebius",
    "GOOGL": "Google",
    "NVDA":  "Nvidia",
    "TSLA":  "Tesla",
    "AMPX":  "Amprius Technologies",
    "AMD":   "AMD",
    "INTC":  "Intel",
    "MSFT":  "Microsoft",
    "AAPL":  "Apple",
    "NFLX":  "Netflix",
}

# Default symbols active when no session config exists yet
DEFAULT_SYMBOLS: list[str] = ["NBIS", "GOOGL"]

# Active symbols — overridden at runtime by session_manager from session_config.json
SYMBOLS: list[str] = DEFAULT_SYMBOLS.copy()

# ── Session config (written by dashboard, read by main loop) ───────────────────
SESSION_CONFIG_PATH = DATA_DIR / "session_config.json"

# ── Strategy registry ──────────────────────────────────────────────────────────
# Each entry describes one runnable strategy.
# status: "live" → selectable | "beta" → selectable (warn) | "soon" → greyed out
AVAILABLE_STRATEGIES: dict[str, dict] = {
    "regime_mean_reversion": {
        "name":        "Regime-Based Mean Reversion",
        "short_name":  "Regime MR",
        "description": "Z-score on 5-min bars, gated by realized-vol stock regime and VIX. "
                       "Enters when price is ≥2 std from its 20-bar mean, exits on TP/SL/EOD.",
        "params": {
            "Z entry": "±2.0σ",
            "Take profit": "+2%",
            "Stop loss": "−1%",
            "Lookback": "20 bars",
            "Bar size": "5 min",
        },
        "module": "main",
        "status": "live",
    },
    "momentum_breakout": {
        "name":        "Momentum Breakout",
        "short_name":  "Momentum",
        "description": "Enters on 20-day high breakout with volume confirmation. "
                       "Trend-following; works best in high-vol regimes.",
        "params": {
            "Breakout window": "20 days",
            "Volume filter": "1.5× avg",
            "Trailing stop": "−3%",
        },
        "module": None,
        "status": "soon",
    },
    "pairs_stat_arb": {
        "name":        "Pairs Stat-Arb",
        "short_name":  "Pairs",
        "description": "Co-integration–based long/short pairs within the same sector. "
                       "Market-neutral; targets spread reversion.",
        "params": {
            "Window": "60 days",
            "Z entry": "±2.5σ",
            "Z exit": "0σ",
        },
        "module": None,
        "status": "soon",
    },
}

# ── Trading hours (Eastern Time) ───────────────────────────────────────────────
MARKET_OPEN_HOUR = 9
MARKET_OPEN_MINUTE = 30
MARKET_CLOSE_HOUR = 15
MARKET_CLOSE_MINUTE = 50   # 10-min buffer before 4 PM
SIGNAL_INTERVAL_MINUTES = 5

# ── Regime thresholds (realized volatility on daily returns, annualized) ───────
REALIZED_VOL_LOW_THRESHOLD = 0.15    # < 15% → low vol, strong mean reversion
REALIZED_VOL_HIGH_THRESHOLD = 0.30   # ≥ 30% → high vol, skip mean reversion

# ── VIX gate thresholds ────────────────────────────────────────────────────────
VIX_LOW_THRESHOLD = 15    # < 15 → full size (1.0×)
VIX_HIGH_THRESHOLD = 25   # > 25 → reduced size (0.5×)

# ── Mean reversion signal parameters ──────────────────────────────────────────
Z_SCORE_ENTRY_THRESHOLD = 2.0   # |Z| ≥ 2.0 triggers entry
LOOKBACK_BARS = 20               # bars for SMA/STD calculation
INTRADAY_BAR_LIMIT = 100         # how many 5-min bars to fetch
DAILY_BAR_LIMIT = 60             # how many daily bars for vol calculation

# ── Position sizing ────────────────────────────────────────────────────────────
BASE_POSITION_SIZE = 5           # base shares before multipliers

# Stock regime share multipliers (applied to base size before VIX mult)
STOCK_REGIME_MULTIPLIERS = {
    "low_vol": 1.0,
    "medium_vol": 0.6,
    "high_vol": 0.0,    # skip
}

# VIX-based position multipliers
VIX_POSITION_MULTIPLIERS = {
    "low_vol": 1.0,
    "medium_vol": 0.8,
    "high_vol": 0.5,
}

# ── Exit parameters ────────────────────────────────────────────────────────────
TAKE_PROFIT_PCT = 0.02    # +2%
STOP_LOSS_PCT = -0.01     # -1%
MAX_HOLD_DAYS = 3

# ── Risk limits ────────────────────────────────────────────────────────────────
MAX_OPEN_POSITIONS = 15
MAX_DAILY_DRAWDOWN_PCT = 0.10   # kill switch at -10% daily P&L

# ── Cache settings ─────────────────────────────────────────────────────────────
CACHE_MAX_AGE_MINUTES = 5

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_FILE = LOGS_DIR / "trading.log"
LOG_LEVEL = "INFO"

# ── Backtest settings ──────────────────────────────────────────────────────────
BACKTEST_START = "2024-01-01"
BACKTEST_END = "2025-05-17"
BACKTEST_INITIAL_CAPITAL = 100_000
