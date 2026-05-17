"""
main.py — Live trading loop for the Regime-Based Mean Reversion system.

Run:
    python main.py

The loop fires every 5 minutes during market hours (9:30 AM – 3:50 PM ET).
All positions are closed by EOD via time-exit logic.
"""

import csv
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import pytz

import config
from data_manager import fetch_5min_bars, fetch_daily_bars, get_vix
from signals import RegimeClassifier, VIXGate, MeanReversionSignal
from position_sizer import PositionSizer
from risk_manager import RiskManager
from executor import OrderManager, ExitManager
from session_manager import get_active_symbols, get_active_strategy

# ── Logging setup ──────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_FILE),
    ],
)
logger = logging.getLogger("main")

ET = pytz.timezone("America/New_York")


# ── Signal CSV helper ──────────────────────────────────────────────────────────

def _write_signal_csv(signal: dict) -> None:
    row = {
        "timestamp": signal.get("timestamp", datetime.utcnow()),
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "z_score": signal.get("z_score"),
        "regime": signal.get("stock_regime"),
        "confidence": signal.get("confidence"),
        "sma_20": signal.get("sma_20"),
        "std_20": signal.get("std_20"),
    }
    write_header = (
        not config.SIGNALS_CSV.exists()
        or config.SIGNALS_CSV.stat().st_size == 0
    )
    with open(config.SIGNALS_CSV, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=row.keys())
        if write_header:
            writer.writeheader()
        writer.writerow(row)


# ── Main loop iteration ────────────────────────────────────────────────────────

def run_iteration(
    regime_clf: RegimeClassifier,
    vix_gate: VIXGate,
    signal_gen: MeanReversionSignal,
    sizer: PositionSizer,
    risk: RiskManager,
    order_mgr: OrderManager,
    exit_mgr: ExitManager,
    daily_pnl: list[float],
) -> None:
    now_et = datetime.now(tz=ET)
    logger.info("── Iteration: %s ──────────────────────────────", now_et.strftime("%H:%M:%S"))

    # ── 1. Market hours gate ───────────────────────────────────────────────────
    if not risk.check_market_hours(now_et):
        logger.info("Outside market hours — sleeping")
        return

    # ── 2. Kill switch ─────────────────────────────────────────────────────────
    if risk.check_max_drawdown(daily_pnl):
        logger.critical("Kill switch active — closing all positions")
        for sym in list(order_mgr.open_positions.keys()):
            bars = fetch_5min_bars(sym)
            if not bars.empty:
                price = float(bars["close"].iloc[-1])
                exit_mgr.execute_exit(sym, price, "kill_switch")
        return

    # ── 3. Fetch VIX ───────────────────────────────────────────────────────────
    vix_info = vix_gate.gate()
    vix = vix_info["vix"]
    logger.info("VIX=%.2f  market_regime=%s  vix_mult=%.2f",
                vix, vix_info["regime"], vix_info["position_multiplier"])

    # ── 4. Exit checks on open positions ──────────────────────────────────────
    for symbol in list(order_mgr.open_positions.keys()):
        bars = fetch_5min_bars(symbol)
        if bars.empty:
            continue
        current_price = float(bars["close"].iloc[-1])
        order_mgr.update_unrealized_pnl(symbol, current_price)
        reason = exit_mgr.run_exit_checks(symbol, current_price, now_et)
        if reason:
            pos = order_mgr.open_positions.get(symbol)
            if pos is None:  # position was just removed by execute_exit
                sign = 1
                entry_p = current_price
            else:
                sign = 1 if pos["side"] == "long" else -1
                entry_p = pos.get("entry_price", current_price)
            pnl = sign * (current_price - entry_p) / entry_p if entry_p else 0.0
            daily_pnl.append(pnl)
            logger.info("Exit triggered for %s: %s  pnl=%.2f%%", symbol, reason, pnl * 100)

    # ── 5. Entry signal loop ───────────────────────────────────────────────────
    # Re-read from session_config.json each iteration so dashboard changes
    # take effect without restarting main.py.
    active_symbols = get_active_symbols()
    for symbol in active_symbols:
        # skip if already holding
        if symbol in order_mgr.open_positions:
            continue

        # fetch data
        intraday = fetch_5min_bars(symbol)
        daily = fetch_daily_bars(symbol)

        if intraday.empty or daily.empty:
            logger.warning("%s: missing data — skipping", symbol)
            continue

        # stock regime
        regime_info = regime_clf.classify(symbol, daily)
        stock_regime = regime_info["regime"]

        # signal
        signal = signal_gen.generate_signal(symbol, intraday, stock_regime=stock_regime)
        _write_signal_csv(signal)

        direction = signal["direction"]
        if direction == 0:
            continue  # neutral — no trade

        # position size
        size_info = sizer.calculate_sizing_breakdown(symbol, stock_regime, vix)
        final_size = size_info["final_size"]

        # risk check
        if not risk.check_position_limits(
            symbol, direction, final_size, order_mgr.open_positions
        ):
            continue

        side = "buy" if direction == 1 else "sell"
        logger.info(
            "ENTRY SIGNAL %s %s | z=%.3f | regime=%s | size=%d",
            side.upper(), symbol,
            signal["z_score"] or 0,
            stock_regime, final_size,
        )
        order_id = order_mgr.submit_order(symbol, final_size, side)
        if order_id:
            # Optimistic fill for paper / sim (will be reconciled next iteration)
            entry_price = float(intraday["close"].iloc[-1])
            order_mgr.open_positions[symbol] = {
                "entry_price": entry_price,
                "qty": final_size,
                "side": "long" if direction == 1 else "short",
                "order_id": order_id,
                "entry_time": now_et,
                "unrealized_pnl": 0.0,
            }

    # ── 6. Reconcile fills ─────────────────────────────────────────────────────
    order_mgr.check_and_update_fills()

    # ── 7. EOD force-close ─────────────────────────────────────────────────────
    if risk.check_day_trade_close(now_et):
        logger.info("EOD: force-closing all remaining positions")
        for sym in list(order_mgr.open_positions.keys()):
            bars = fetch_5min_bars(sym)
            price = float(bars["close"].iloc[-1]) if not bars.empty else 0.0
            exit_mgr.execute_exit(sym, price, "eod_close")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    active_strategy = get_active_strategy()
    active_symbols = get_active_symbols()
    logger.info("=" * 70)
    logger.info("LIVE LOOP STARTING | strategy=%s | paper=%s",
                active_strategy, config.PAPER_TRADING)
    logger.info("Universe: %s", active_symbols)
    logger.info("(Universe re-read from session_config.json each 5-min iteration)")
    logger.info("=" * 70)

    regime_clf = RegimeClassifier()
    vix_gate = VIXGate()
    signal_gen = MeanReversionSignal()
    sizer = PositionSizer()
    risk = RiskManager()
    order_mgr = OrderManager()
    exit_mgr = ExitManager(order_mgr)
    daily_pnl: list[float] = []

    while True:
        try:
            run_iteration(
                regime_clf, vix_gate, signal_gen,
                sizer, risk, order_mgr, exit_mgr,
                daily_pnl,
            )
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt — shutting down")
            break
        except Exception as e:
            logger.exception("Unhandled error in main loop: %s", e)

        sleep_seconds = config.SIGNAL_INTERVAL_MINUTES * 60
        logger.info("Sleeping %d seconds …", sleep_seconds)
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()
