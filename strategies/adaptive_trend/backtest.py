"""
backtest.py — Walk-forward backtest for AdaptiveTrend on Alpaca crypto data.

Usage:
    PYTHONPATH=/path/to/project python3 strategies/adaptive_trend/backtest.py \\
        --start 2023-01-01 --end 2024-12-31 [--fetch]

Steps:
    1. --fetch (optional): pull 6h bars from Alpaca and save to data/bars/crypto_6h/
    2. Load cached Parquet bars
    3. Walk forward bar-by-bar, generating signals and simulating trades
    4. Write at_backtest_trades.csv and at_backtest_report.csv to outputs/
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# Allow running as __main__ from any directory
_project_root = Path(__file__).resolve().parents[2]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import config  # noqa: E402
from strategies.adaptive_trend import adaptive_trend_config as cfg  # noqa: E402
from strategies.adaptive_trend.strategy import (  # noqa: E402
    Direction, compute_atr, compute_momentum, compute_trailing_stop,
    compute_trailing_sharpe, should_close,
)
from strategies.adaptive_trend.universe import fetch_all_ohlcv, save_bars, load_bars  # noqa: E402

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ── Simulation helpers ─────────────────────────────────────────────────────────

def _apply_costs(pnl: float, notional: float) -> float:
    """Deduct round-trip taker fee + slippage from raw PnL."""
    cost_bps = (cfg.TAKER_FEE_BPS + cfg.SLIPPAGE_BPS) * 2  # entry + exit
    return pnl - notional * cost_bps / 10_000


def _simulate_bar_by_bar(
    bars:        dict[str, pd.DataFrame],
    start:       datetime,
    end:         datetime,
    initial_eq:  float = 100_000.0,
) -> list[dict]:
    """
    Walk forward over 6h bars. At each bar:
      - For open positions: update trailing stop, close if triggered.
      - Monthly: rebalance (pick long/short universe, re-score signals).
      - Generate signals on eligible symbols; open new positions.
    Returns list of trade dicts.
    """

    # Align all series to a common 6h index
    all_dates = sorted(set().union(*[set(df.index) for df in bars.values()]))
    all_dates = [d for d in all_dates if start <= d <= end]
    if not all_dates:
        log.error("No bars in range %s – %s", start, end)
        return []

    equity    = initial_eq
    positions: dict[str, dict] = {}   # symbol → {direction, entry, trail_stop, notional, qty}
    trades:    list[dict]      = []
    last_month = -1

    for bar_dt in all_dates:
        # ── Close triggered positions ──────────────────────────────────────────
        for sym in list(positions.keys()):
            pos = positions[sym]
            if bar_dt not in bars[sym].index:
                continue
            row       = bars[sym].loc[bar_dt]
            price     = row["close"]
            atr_s     = compute_atr(bars[sym]["high"], bars[sym]["low"], bars[sym]["close"])
            atr_val   = atr_s.loc[:bar_dt].iloc[-1] if not atr_s.empty else 0.0

            # Update trailing stop
            pos["trail_stop"] = compute_trailing_stop(
                price, pos["trail_stop"], atr_val, pos["direction"]
            )

            triggered = (
                (pos["direction"] == Direction.LONG  and price <= pos["trail_stop"]) or
                (pos["direction"] == Direction.SHORT and price >= pos["trail_stop"])
            )
            if triggered:
                raw_pnl = (price - pos["entry"]) * pos["qty"] if pos["direction"] == Direction.LONG \
                     else (pos["entry"] - price) * pos["qty"]
                net_pnl = _apply_costs(raw_pnl, pos["notional"])
                equity += net_pnl
                trades.append({
                    "symbol":    sym,
                    "direction": pos["direction"].value,
                    "entry_dt":  pos["entry_dt"].isoformat(),
                    "exit_dt":   bar_dt.isoformat(),
                    "entry":     pos["entry"],
                    "exit":      price,
                    "qty":       pos["qty"],
                    "notional":  pos["notional"],
                    "pnl":       round(net_pnl, 4),
                    "exit_type": "trail_stop",
                })
                del positions[sym]

        # ── Monthly rebalance: pick universe and generate signals ───────────────
        if bar_dt.month != last_month:
            last_month = bar_dt.month

            # Compute dollar-volume scores up to bar_dt
            dv_scores: dict[str, float] = {}
            for sym, df in bars.items():
                hist = df.loc[:bar_dt]
                if len(hist) < 5:
                    continue
                dv_scores[sym] = (hist["close"] * hist["volume"]).tail(30).mean()

            long_universe  = sorted(dv_scores, key=lambda s: dv_scores[s], reverse=True)[:cfg.K_LONG]

            mom_scores: dict[str, float] = {}
            for sym in dv_scores:
                hist  = bars[sym]["close"].loc[:bar_dt]
                m_ser = compute_momentum(hist)
                if not m_ser.empty:
                    v = m_ser.iloc[-1]
                    if not pd.isna(v):
                        mom_scores[sym] = v
            short_universe = sorted(mom_scores, key=lambda s: mom_scores[s])[:cfg.K_SHORT]

            # Compute Sharpe and generate signals
            n_long  = len(long_universe)
            n_short = len(short_universe)
            long_alloc  = equity * cfg.LAMBDA_LONG  / n_long  if n_long  else 0.0
            short_alloc = equity * cfg.LAMBDA_SHORT / n_short if n_short else 0.0

            for sym in set(long_universe + short_universe):
                if sym in positions:
                    continue
                df   = bars[sym]
                hist = df.loc[:bar_dt]
                if len(hist) < cfg.LOOKBACK_BARS + cfg.ATR_PERIOD + 5:
                    continue

                close  = hist["close"]
                high   = hist["high"]
                low    = hist["low"]
                price  = close.iloc[-1]

                atr_s    = compute_atr(high, low, close)
                mom_s    = compute_momentum(close)
                atr_val  = atr_s.iloc[-1]
                mom_val  = mom_s.iloc[-1]

                if pd.isna(atr_val) or pd.isna(mom_val):
                    continue

                returns = close.pct_change()
                sharpe  = compute_trailing_sharpe(returns)

                if sym in long_universe and mom_val > cfg.ENTRY_THRESHOLD and sharpe >= cfg.GAMMA_LONG:
                    direction  = Direction.LONG
                    trail_stop = price - cfg.ALPHA * atr_val
                    notional   = long_alloc
                elif sym in short_universe and mom_val < -cfg.SHORT_THRESHOLD and sharpe >= cfg.GAMMA_SHORT:
                    direction  = Direction.SHORT
                    trail_stop = price + cfg.ALPHA * atr_val
                    notional   = short_alloc
                else:
                    continue

                if notional < 1.0:
                    continue

                qty = notional / price
                positions[sym] = {
                    "direction":  direction,
                    "entry":      price,
                    "trail_stop": trail_stop,
                    "notional":   notional,
                    "qty":        qty,
                    "entry_dt":   bar_dt,
                }

    # ── Force-close any open positions at end of backtest ─────────────────────
    last_dt = all_dates[-1]
    for sym, pos in positions.items():
        df = bars.get(sym)
        if df is None or last_dt not in df.index:
            continue
        price   = df.loc[last_dt, "close"]
        raw_pnl = (price - pos["entry"]) * pos["qty"] if pos["direction"] == Direction.LONG \
             else (pos["entry"] - price) * pos["qty"]
        net_pnl = _apply_costs(raw_pnl, pos["notional"])
        equity += net_pnl
        trades.append({
            "symbol":    sym,
            "direction": pos["direction"].value,
            "entry_dt":  pos["entry_dt"].isoformat(),
            "exit_dt":   last_dt.isoformat(),
            "entry":     pos["entry"],
            "exit":      price,
            "qty":       pos["qty"],
            "notional":  pos["notional"],
            "pnl":       round(net_pnl, 4),
            "exit_type": "eod_force",
        })

    return trades


# ── Metrics ────────────────────────────────────────────────────────────────────

def _compute_metrics(trades: list[dict], initial_eq: float, start: datetime, end: datetime) -> dict:
    if not trades:
        return {"error": "no trades"}

    df = pd.DataFrame(trades)
    df["entry_dt"] = pd.to_datetime(df["entry_dt"])
    df["exit_dt"]  = pd.to_datetime(df["exit_dt"])
    df.sort_values("exit_dt", inplace=True)

    total_pnl = df["pnl"].sum()
    final_eq  = initial_eq + total_pnl
    total_ret = (final_eq - initial_eq) / initial_eq

    # Daily equity curve for Sharpe / drawdown
    df["date"] = df["exit_dt"].dt.date
    daily_pnl  = df.groupby("date")["pnl"].sum()
    eq_curve   = initial_eq + daily_pnl.cumsum()
    daily_ret  = daily_pnl / initial_eq

    bars_per_year = 365
    sharpe = (daily_ret.mean() / daily_ret.std(ddof=1) * np.sqrt(bars_per_year)
              if daily_ret.std(ddof=1) > 0 else 0.0)

    roll_max = eq_curve.cummax()
    drawdown = (eq_curve - roll_max) / roll_max
    mdd      = drawdown.min()

    n_days = max((end - start).days, 1)
    ann_ret = (1 + total_ret) ** (365 / n_days) - 1

    calmar = ann_ret / abs(mdd) if mdd != 0 else 0.0

    winners = df[df["pnl"] > 0]
    losers  = df[df["pnl"] < 0]

    return {
        "initial_equity":    initial_eq,
        "final_equity":      round(final_eq, 2),
        "total_return_pct":  round(total_ret * 100, 2),
        "ann_return_pct":    round(ann_ret * 100, 2),
        "sharpe_ratio":      round(sharpe, 3),
        "max_drawdown_pct":  round(mdd * 100, 2),
        "calmar_ratio":      round(calmar, 3),
        "total_trades":      len(df),
        "win_rate_pct":      round(len(winners) / len(df) * 100, 1),
        "avg_win":           round(winners["pnl"].mean(), 2) if len(winners) else 0,
        "avg_loss":          round(losers["pnl"].mean(),  2) if len(losers)  else 0,
        "trail_stop_exits":  int((df["exit_type"] == "trail_stop").sum()),
        "force_exits":       int((df["exit_type"] == "eod_force").sum()),
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="AdaptiveTrend backtest")
    ap.add_argument("--start",    default="2023-01-01", help="Start date YYYY-MM-DD")
    ap.add_argument("--end",      default="2024-12-31", help="End date YYYY-MM-DD")
    ap.add_argument("--equity",   type=float, default=100_000.0, help="Starting equity")
    ap.add_argument("--fetch",    action="store_true", help="Re-fetch bars from Alpaca before running")
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end   = datetime.strptime(args.end,   "%Y-%m-%d").replace(tzinfo=timezone.utc)

    if args.fetch:
        log.info("Fetching bars from Alpaca (%s → %s)…", args.start, args.end)
        bars = fetch_all_ohlcv(start=start, end=end)
        save_bars(bars)
    else:
        log.info("Loading cached bars from %s", cfg.bars_dir())
        bars = load_bars()
        if not bars:
            log.error("No cached bars found. Run with --fetch first.")
            sys.exit(1)

    log.info("Running simulation on %d symbols…", len(bars))
    trades = _simulate_bar_by_bar(bars, start, end, initial_eq=args.equity)
    log.info("Trades generated: %d", len(trades))

    metrics = _compute_metrics(trades, args.equity, start, end)

    # ── Print report ───────────────────────────────────────────────────────────
    print("\n─── AdaptiveTrend Backtest Report ───")
    for k, v in metrics.items():
        print(f"  {k:<25} {v}")
    print()

    # ── Save outputs ───────────────────────────────────────────────────────────
    if trades:
        trades_path = cfg.backtest_trades_path()
        pd.DataFrame(trades).to_csv(trades_path, index=False)
        log.info("Trades → %s", trades_path)

    report_path = cfg.backtest_report_path()
    pd.DataFrame([metrics]).to_csv(report_path, index=False)
    log.info("Report → %s", report_path)


if __name__ == "__main__":
    main()
