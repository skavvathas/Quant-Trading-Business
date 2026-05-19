"""
orb_main.py — ORB strategy live trading loop (Alpaca paper / live).

Timeline per trading day:
  ~8:00 AM    rebuild overnight watchlist
   9:35 AM    morning scan → top-20 signals → submit stop-entry orders
  every 60 s  sync fills + stop-loss hits, update orb_state.json
  15:50 PM    close all open positions, cancel unfilled entries

Run:
    python orb_main.py
"""

import json
import logging
import time
from datetime import datetime, time as dtime
from pathlib import Path

import pytz

import config
from strategies.orb import orb_config
from strategies.orb.strategy import compute_shares
from strategies.orb.universe import build_watchlist, load_watchlist
from strategies.orb.scanner import run_scan
from strategies.orb.executor import ORBExecutor

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")
STATE_FILE = config.DATA_DIR / "orb_state.json"


def _alpaca_account() -> dict:
    try:
        from alpaca.trading.client import TradingClient
        acc = TradingClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
            paper=config.PAPER_TRADING,
        ).get_account()
        equity      = float(acc.equity)
        last_equity = float(acc.last_equity)
        today_pl    = equity - last_equity
        return {
            "equity":       equity,
            "last_equity":  last_equity,
            "buying_power": float(acc.buying_power),
            "today_pl":     round(today_pl, 2),
            "today_pl_pct": round(today_pl / last_equity * 100 if last_equity else 0.0, 3),
        }
    except Exception as e:
        logger.warning("Account fetch failed: %s", e)
        return {"equity": 0.0, "last_equity": 0.0, "buying_power": 0.0, "today_pl": 0.0, "today_pl_pct": 0.0}


def _write_state(executor: ORBExecutor, signals: list, account: dict) -> None:
    equity = account.get("equity") or 25_000.0
    pending_syms = {p.signal.ticker for p in executor.pending_entries.values()}
    open_syms    = set(executor.open_positions.keys())

    def _status(sym: str) -> str:
        if sym in open_syms:    return "filled"
        if sym in pending_syms: return "pending"
        return "not_triggered"

    state = {
        "updated_at": datetime.now(tz=ET).isoformat(),
        "account":    account,
        "signals": [
            {
                "symbol":      s.ticker,
                "direction":   s.direction.value,
                "entry_price": round(s.entry_price, 4),
                "stop_loss":   round(s.stop_loss,   4),
                "relvol":      round(s.relative_volume, 2),
                "atr":         round(s.atr, 4),
                "shares":      compute_shares(s.entry_price, s.stop_loss, equity),
                "status":      _status(s.ticker),
            }
            for s in signals
        ],
        "open_positions": [
            {
                "symbol":      pos.ticker,
                "direction":   pos.direction.value,
                "entry_price": round(pos.entry_price,     4),
                "stop_loss":   round(pos.stop_loss_price, 4),
                "qty":         pos.qty,
                "entry_time":  pos.entry_time.isoformat(),
            }
            for pos in executor.open_positions.values()
        ],
    }
    STATE_FILE.write_text(json.dumps(state, indent=2))
    logger.debug("State written → %s", STATE_FILE)


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(config.LOGS_DIR / "orb_main.log"),
        ],
    )
    logger.info("ORB live loop starting  (paper=%s)", config.PAPER_TRADING)

    try:
        watchlist = load_watchlist()
        logger.info("Watchlist loaded: %d symbols", len(watchlist))
    except FileNotFoundError:
        logger.info("No watchlist on disk — building now (takes a few minutes)…")
        watchlist = build_watchlist()

    account   = _alpaca_account()
    executor  = ORBExecutor(capital=account.get("equity") or 25_000.0)
    signals: list = []
    scan_done = False
    eod_done  = False
    today     = None

    while True:
        now     = datetime.now(tz=ET)
        t       = now.time()
        weekday = now.weekday()

        # ── New calendar day: reset state ──────────────────────────────────────
        if now.date() != today:
            today     = now.date()
            scan_done = False
            eod_done  = False
            signals   = []
            account   = _alpaca_account()
            executor  = ORBExecutor(capital=account.get("equity") or 25_000.0)
            logger.info("New day %s  |  account equity $%.0f", today, executor.capital)

        if weekday >= 5:
            time.sleep(600)
            continue

        # ── 8:00 AM: rebuild overnight watchlist ───────────────────────────────
        if t.hour == 8 and t.minute < 2 and not scan_done:
            logger.info("Pre-market: rebuilding watchlist…")
            try:
                watchlist = build_watchlist()
            except Exception as e:
                logger.error("Watchlist rebuild failed: %s", e)

        # ── 9:35 AM: scan + place stop orders ─────────────────────────────────
        if not scan_done and dtime(9, 35) <= t < dtime(9, 40):
            logger.info("9:35 AM scan…")
            try:
                signals = run_scan(watchlist)
                executor.submit_entry_orders(signals)
            except Exception as e:
                logger.error("Scan/order submission failed: %s", e)
            scan_done = True
            account = _alpaca_account()
            _write_state(executor, signals, account)

        # ── Market hours: sync every 60 s ─────────────────────────────────────
        if scan_done and not eod_done and dtime(9, 40) <= t < dtime(15, 50):
            try:
                executor.sync_fills()
                executor.sync_stop_loss_hits()
                account = _alpaca_account()
                _write_state(executor, signals, account)
            except Exception as e:
                logger.error("Sync error: %s", e)
            time.sleep(60)
            continue

        # ── 15:50 PM: EOD close ────────────────────────────────────────────────
        if not eod_done and t >= dtime(15, 50):
            logger.info("EOD close…")
            try:
                executor.close_all_positions()
                executor.cancel_pending_entries()
                account = _alpaca_account()
                _write_state(executor, signals, account)
            except Exception as e:
                logger.error("EOD close failed: %s", e)
            eod_done = True
            logger.info("Day complete. Waiting for next session.")

        time.sleep(30)


if __name__ == "__main__":
    run()
