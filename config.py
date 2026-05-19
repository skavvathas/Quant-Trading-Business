"""
config.py — Project-level configuration for the ORB trading system.

Paths, Alpaca credentials, and shared constants live here.
Strategy-specific parameters are in strategies/orb/orb_config.py.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Project paths ──────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
DATA_DIR    = BASE_DIR / "data"
LOGS_DIR    = BASE_DIR / "logs"
OUTPUTS_DIR = BASE_DIR / "outputs"

for _d in (DATA_DIR, LOGS_DIR, OUTPUTS_DIR):
    _d.mkdir(exist_ok=True)

# ── Alpaca credentials ─────────────────────────────────────────────────────────
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY",    "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL   = os.getenv("ALPACA_BASE_URL",   "https://paper-api.alpaca.markets")
PAPER_TRADING     = True   # flip to False for live

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_FILE  = LOGS_DIR / "orb.log"
LOG_LEVEL = "INFO"
