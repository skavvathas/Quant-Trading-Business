"""
session_manager.py — Read/write the daily trading plan (strategy + symbols).

The dashboard writes session_config.json via save_session_config().
main.py reads it at startup (and each iteration) via get_active_symbols() /
get_active_strategy() so the live loop always reflects the dashboard selection.
"""

import json
import logging
from datetime import datetime
from typing import Optional

import config

logger = logging.getLogger(__name__)


def load_session_config() -> dict:
    """
    Return the saved session config dict.

    Falls back to defaults when the file is missing or corrupt.
    """
    path = config.SESSION_CONFIG_PATH
    if not path.exists():
        return _defaults()
    try:
        with open(path) as fh:
            data = json.load(fh)
        # Validate symbols against the full universe
        data["symbols"] = [
            s for s in data.get("symbols", config.DEFAULT_SYMBOLS)
            if s in config.FULL_UNIVERSE
        ] or config.DEFAULT_SYMBOLS
        # Validate strategy key
        if data.get("strategy") not in config.AVAILABLE_STRATEGIES:
            data["strategy"] = "regime_mean_reversion"
        return data
    except Exception as e:
        logger.warning("session_config read error: %s — using defaults", e)
        return _defaults()


def save_session_config(strategy: str, symbols: list[str]) -> None:
    """
    Persist today's trading plan so main.py picks it up on its next iteration.
    """
    if strategy not in config.AVAILABLE_STRATEGIES:
        raise ValueError(f"Unknown strategy: {strategy}")
    invalid = [s for s in symbols if s not in config.FULL_UNIVERSE]
    if invalid:
        raise ValueError(f"Unknown symbols: {invalid}")
    if not symbols:
        raise ValueError("Symbol list cannot be empty")

    data = {
        "strategy": strategy,
        "symbols": symbols,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    config.SESSION_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(config.SESSION_CONFIG_PATH, "w") as fh:
        json.dump(data, fh, indent=2)
    logger.info("Session config saved: %s | %s", strategy, symbols)


def get_active_symbols() -> list[str]:
    """Return today's active symbol list (used by main.py each iteration)."""
    return load_session_config()["symbols"]


def get_active_strategy() -> str:
    """Return today's active strategy key (used by main.py at startup)."""
    return load_session_config()["strategy"]


def get_updated_at() -> Optional[str]:
    cfg = load_session_config()
    return cfg.get("updated_at")


def _defaults() -> dict:
    return {
        "strategy": "regime_mean_reversion",
        "symbols": config.DEFAULT_SYMBOLS.copy(),
        "updated_at": None,
    }
