"""
orb_qqq_main.py — ORB QQQ / TQQQ live trading loop.

Uses the *optimised* variant: ATR stop (10% of 14-day ATR), hold until EOD.
Instruments: QQQ + TQQQ (both traded every session).

Timeline per trading day:
  9:35 AM   fetch opening candle + ATR14 → generate signals → submit market entries
  every 60s sync fills → attach stop-loss orders; check SL hits
  15:55 PM  close all open positions, cancel any unfilled entries

Run:
    python orb_qqq_main.py
    python orb_qqq_main.py --variant baseline   # use baseline instead of optimised
    python orb_qqq_main.py --equity 50000
"""

import json
import logging
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path

import pytz

import config
from strategies.orb_qqq.live_trader import OrbQQQLiveTrader, STATE_FILE

logger = logging.getLogger(__name__)
ET       = pytz.timezone("America/New_York")
PID_FILE = config.DATA_DIR / "orb_qqq_main.pid"


# ── PID management ─────────────────────────────────────────────────────────────

def _write_pid() -> None:
    PID_FILE.write_text(str(Path(__file__).resolve().stat().st_ino) + f"\n{__import__('os').getpid()}")

def _clear_pid() -> None:
    PID_FILE.unlink(missing_ok=True)


# ── Account helper ─────────────────────────────────────────────────────────────

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
        return {"equity": 0.0, "last_equity": 0.0, "buying_power": 0.0,
                "today_pl": 0.0, "today_pl_pct": 0.0}


# ── Main loop ──────────────────────────────────────────────────────────────────

def run(variant: str = "optimised", equity: float = 25_000.0,
        instruments: list[str] | None = None) -> None:
    instruments = instruments or ["TQQQ"]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.LOGS_DIR / "orb_qqq_main.log"),
        ],
    )
    logger.info("=" * 60)
    logger.info("ORB live loop  instruments=%s  variant=%s  paper=%s",
                instruments, variant, config.PAPER_TRADING)
    logger.info("=" * 60)

    _write_pid()

    account  = _alpaca_account()
    capital  = account.get("equity") or equity
    trader   = OrbQQQLiveTrader(capital=capital, variant=variant, instruments=instruments)
    scan_done  = False
    eod_done   = False
    today      = None
    last_hb    = datetime.now(tz=ET)

    try:
        while True:
            now     = datetime.now(tz=ET)
            t       = now.time()
            weekday = now.weekday()

            # ── Heartbeat every 5 min ──────────────────────────────────────────
            if (now - last_hb).total_seconds() >= 300:
                n_pos = len(trader.positions)
                if eod_done:
                    phase = "day complete"
                elif scan_done:
                    phase = f"market hours — {n_pos} open position(s)"
                else:
                    phase = "pre-market — waiting for 9:35 AM"
                logger.info("♥ heartbeat  |  %s", phase)
                last_hb = now

            # ── New calendar day: reset ────────────────────────────────────────
            if now.date() != today:
                today     = now.date()
                scan_done = False
                eod_done  = False
                account   = _alpaca_account()
                capital   = account.get("equity") or equity
                trader    = OrbQQQLiveTrader(capital=capital, variant=variant)
                logger.info("New day %s  |  equity=$%.0f", today, capital)

            if weekday >= 5:
                time.sleep(600)
                continue

            # ── 9:35 AM scan + entry ───────────────────────────────────────────
            if not scan_done and dtime(9, 35) <= t < dtime(9, 40):
                logger.info("9:35 AM — generating signals…")
                try:
                    trader.generate_signals()
                    trader.submit_entries()
                except Exception as e:
                    logger.error("Signal/entry error: %s", e)
                scan_done = True
                account   = _alpaca_account()
                trader.write_state(account, phase="entries_submitted")

            # ── Market hours sync every 60 s ──────────────────────────────────
            if scan_done and not eod_done and dtime(9, 40) <= t < dtime(15, 55):
                try:
                    trader.sync()
                    account = _alpaca_account()
                    trader.write_state(account, phase="active")
                except Exception as e:
                    logger.error("Sync error: %s", e)
                time.sleep(60)
                continue

            # ── 15:55 PM EOD close ─────────────────────────────────────────────
            if not eod_done and t >= dtime(15, 55):
                logger.info("15:55 PM — EOD close")
                try:
                    trader.close_all()
                    account = _alpaca_account()
                    trader.write_state(account, phase="eod_closed")
                except Exception as e:
                    logger.error("EOD close error: %s", e)
                eod_done = True
                logger.info("Day complete. Waiting for next session.")

            time.sleep(30)

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt — shutting down.")
    finally:
        _clear_pid()
        STATE_FILE.unlink(missing_ok=True)
        logger.info("orb_qqq_main.py stopped.")


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ORB QQQ/TQQQ live trading loop")
    parser.add_argument("--variant", default="optimised",
                        choices=["optimised", "baseline"],
                        help="optimised: ATR stop + EOD  |  baseline: candle stop + 10R")
    parser.add_argument("--equity", default=25_000.0, type=float,
                        help="Starting equity fallback if Alpaca account fetch fails")
    parser.add_argument("--symbols", default="TQQQ",
                        help="Comma-separated list of instruments to trade (default: TQQQ)")
    args = parser.parse_args()
    instruments = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    run(variant=args.variant, equity=args.equity, instruments=instruments)
