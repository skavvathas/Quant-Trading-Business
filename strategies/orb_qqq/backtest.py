"""
backtest.py — Walk-forward backtest for the ORB QQQ/TQQQ strategy.

Replicates the exact methodology from Zarattini & Aziz (2023/2025).

Usage
-----
# Download data first (one-time):
PYTHONPATH=. python3 strategies/orb_qqq/backtest.py \\
    --instrument QQQ --start 2016-01-01 --end 2023-02-17 --fetch

# Run baseline backtest only (no fetch):
PYTHONPATH=. python3 strategies/orb_qqq/backtest.py \\
    --instrument TQQQ --start 2016-01-01 --end 2023-02-17

# Run both variants:
PYTHONPATH=. python3 strategies/orb_qqq/backtest.py \\
    --instrument QQQ --start 2016-01-01 --end 2023-02-17 --variants both

Outputs
-------
outputs/orbqqq_{instrument}_{variant}_trades.csv  — one row per trade
outputs/orbqqq_{instrument}_{variant}_report.csv  — performance summary
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytz

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import config  # noqa: E402
from strategies.orb_qqq import orb_qqq_config as cfg  # noqa: E402
from strategies.orb_qqq.strategy import (  # noqa: E402
    Direction, generate_signal, simulate_day,
)
from strategies.orb_qqq.data_fetcher import fetch_bars, load_5min, load_daily  # noqa: E402

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

ET = pytz.timezone("America/New_York")


# ── ATR helper ─────────────────────────────────────────────────────────────────

def _compute_atr(daily: pd.DataFrame, as_of: pd.Timestamp, period: int = cfg.ATR_PERIOD) -> float:
    """14-day average true range from daily bars, up to (not including) as_of."""
    hist = daily[daily.index.normalize() < as_of.normalize()].tail(period + 1)
    if len(hist) < 2:
        return 0.0
    prev_close = hist["close"].shift(1)
    tr = pd.concat([
        hist["high"] - hist["low"],
        (hist["high"] - prev_close).abs(),
        (hist["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return float(tr.dropna().tail(period).mean())


# ── Daily simulation ───────────────────────────────────────────────────────────

def _run_day(
    symbol:  str,
    date:    pd.Timestamp,
    bars5:   pd.DataFrame,       # all 5-min bars for the day (ET aware)
    atr14:   float,
    capital: float,
    variant: str,
) -> dict | None:
    """
    Simulate one trading day. Returns a trade dict or None (no trade).
    """
    # Require at least 2 candles
    if len(bars5) < 2:
        return None

    c1      = bars5.iloc[0]   # first 5-min candle (9:30–9:35)
    c2_open = float(bars5.iloc[1]["open"])  # entry = open of 2nd candle

    signal = generate_signal(
        symbol    = symbol,
        c1        = c1,
        c2_open   = c2_open,
        capital   = capital,
        atr14     = atr14,
        variant   = variant,
    )
    if signal is None:
        return None

    # Bars available for intraday simulation (from 2nd candle onward)
    intraday = bars5.iloc[1:]
    result   = simulate_day(signal, intraday)

    gross_pnl = result["pnl_per_share"] * signal.shares
    commission = cfg.COMMISSION * signal.shares * 2   # entry + exit
    net_pnl    = gross_pnl - commission

    return {
        "date":           date.date().isoformat(),
        "symbol":         symbol,
        "variant":        variant,
        "direction":      signal.direction.value,
        "entry":          signal.entry,
        "stop":           signal.stop,
        "target":         round(signal.target, 4),
        "exit_price":     result["exit_price"],
        "exit_type":      result["exit_type"],
        "shares":         signal.shares,
        "risk_per_share": signal.risk_per_share,
        "r_multiple":     round(result["pnl_per_share"] / signal.risk_per_share, 3)
                          if signal.risk_per_share > 0 else 0.0,
        "gross_pnl":      round(gross_pnl, 2),
        "commission":     round(commission, 2),
        "net_pnl":        round(net_pnl, 2),
        "bars_held":      result["bars_held"],
        "capital_before": round(capital, 2),
    }


# ── Main backtest loop ─────────────────────────────────────────────────────────

def run_backtest(
    symbol:    str,
    start:     str,
    end:       str,
    variant:   str   = "baseline",
    capital:   float = cfg.STARTING_CAPITAL,
) -> tuple[list[dict], pd.Series, dict]:
    """
    Run the full walk-forward backtest.

    Returns
    -------
    trades   : list of trade dicts
    equity   : pd.Series of daily equity (indexed by date)
    metrics  : summary performance dict
    """
    bars5  = load_5min(symbol)
    daily  = load_daily(symbol)

    # Filter to ET session hours and date range
    bars5 = bars5.tz_convert(ET)
    bars5 = bars5.between_time("09:30", "15:59")

    start_ts = pd.Timestamp(start, tz=ET)
    end_ts   = pd.Timestamp(end,   tz=ET)
    bars5    = bars5.loc[start_ts:end_ts]

    if daily.index.tzinfo is None:
        daily = daily.tz_localize("UTC").tz_convert(ET)
    else:
        daily = daily.tz_convert(ET)

    # Group 5-min bars by trading date
    dates = sorted(bars5.index.normalize().unique())

    trades:      list[dict] = []
    equity:      dict       = {}
    current_cap: float      = capital

    for day_ts in dates:
        day_bars = bars5[bars5.index.normalize() == day_ts]
        atr14    = _compute_atr(daily, day_ts) if variant == "optimised" else 0.0

        trade = _run_day(
            symbol  = symbol,
            date    = day_ts,
            bars5   = day_bars,
            atr14   = atr14,
            capital = current_cap,
            variant = variant,
        )

        if trade is not None:
            current_cap += trade["net_pnl"]
            trades.append(trade)

        equity[day_ts.date()] = current_cap

    equity_series = pd.Series(equity)
    metrics       = _compute_metrics(trades, capital, start, end)

    return trades, equity_series, metrics


# ── Metrics ────────────────────────────────────────────────────────────────────

def _compute_metrics(
    trades: list[dict],
    initial: float,
    start: str,
    end: str,
) -> dict:
    if not trades:
        return {"error": "no trades"}

    df = pd.DataFrame(trades)
    df["date"] = pd.to_datetime(df["date"])
    df.sort_values("date", inplace=True)

    total_pnl = df["net_pnl"].sum()
    final_eq  = initial + total_pnl
    total_ret = (final_eq - initial) / initial

    # Daily equity curve
    daily_pnl = df.groupby("date")["net_pnl"].sum()
    eq_curve  = initial + daily_pnl.cumsum()
    daily_ret = daily_pnl / initial

    n_days  = max((pd.Timestamp(end) - pd.Timestamp(start)).days, 1)
    ann_ret = (1 + total_ret) ** (365 / n_days) - 1

    sharpe = (daily_ret.mean() / daily_ret.std(ddof=1) * np.sqrt(252)
              if daily_ret.std(ddof=1) > 0 else 0.0)

    roll_max = eq_curve.cummax()
    mdd      = ((eq_curve - roll_max) / roll_max).min()
    calmar   = ann_ret / abs(mdd) if mdd != 0 else 0.0

    winners = df[df["net_pnl"] > 0]
    losers  = df[df["net_pnl"] < 0]

    exit_counts = df["exit_type"].value_counts().to_dict()

    return {
        "instrument":        df["symbol"].iloc[0],
        "variant":           df["variant"].iloc[0],
        "start":             start,
        "end":               end,
        "initial_equity":    initial,
        "final_equity":      round(final_eq, 2),
        "total_return_pct":  round(total_ret * 100, 2),
        "ann_return_pct":    round(ann_ret * 100, 2),
        "sharpe_ratio":      round(sharpe, 3),
        "max_drawdown_pct":  round(mdd * 100, 2),
        "calmar_ratio":      round(calmar, 3),
        "total_trades":      len(df),
        "long_trades":       int((df["direction"] == "long").sum()),
        "short_trades":      int((df["direction"] == "short").sum()),
        "win_rate_pct":      round(len(winners) / len(df) * 100, 1),
        "avg_win":           round(winners["net_pnl"].mean(), 2) if len(winners) else 0,
        "avg_loss":          round(losers["net_pnl"].mean(),  2) if len(losers)  else 0,
        "avg_r_multiple":    round(df["r_multiple"].mean(), 3),
        "target_exits":      exit_counts.get("target", 0),
        "stop_exits":        exit_counts.get("stop", 0),
        "eod_exits":         exit_counts.get("eod", 0),
        "total_commission":  round(df["commission"].sum(), 2),
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="ORB QQQ/TQQQ backtest")
    ap.add_argument("--instrument", default="QQQ",
                    choices=["QQQ", "TQQQ"],
                    help="Instrument to backtest")
    ap.add_argument("--start",     default="2016-01-01",
                    help="Backtest start date YYYY-MM-DD")
    ap.add_argument("--end",       default="2023-02-17",
                    help="Backtest end date YYYY-MM-DD (paper end date)")
    ap.add_argument("--equity",    type=float, default=cfg.STARTING_CAPITAL,
                    help="Starting equity (default: $25,000 as in paper)")
    ap.add_argument("--variants",  default="both",
                    choices=["baseline", "optimised", "both"],
                    help="Which variant(s) to run")
    ap.add_argument("--fetch",     action="store_true",
                    help="Download bars from Alpaca before running")
    args = ap.parse_args()

    if args.fetch:
        log.info("Fetching bars for %s (%s → %s)…", args.instrument, args.start, args.end)
        fetch_bars([args.instrument], args.start, args.end)

    variants = (
        ["baseline", "optimised"] if args.variants == "both"
        else [args.variants]
    )

    for variant in variants:
        log.info("Running %s %s backtest…", args.instrument, variant.upper())
        try:
            trades, equity, metrics = run_backtest(
                symbol   = args.instrument,
                start    = args.start,
                end      = args.end,
                variant  = variant,
                capital  = args.equity,
            )
        except FileNotFoundError as e:
            log.error("%s — run with --fetch first.", e)
            continue

        # ── Print ──────────────────────────────────────────────────────────────
        print(f"\n{'='*52}")
        print(f"  ORB {args.instrument} — {variant.upper()}")
        print(f"{'='*52}")
        for k, v in metrics.items():
            print(f"  {k:<25} {v}")
        print(f"{'='*52}\n")

        # ── Save ───────────────────────────────────────────────────────────────
        if trades:
            t_path = cfg.backtest_trades_path(args.instrument, variant)
            pd.DataFrame(trades).to_csv(t_path, index=False)
            log.info("Trades → %s", t_path)

        r_path = cfg.backtest_report_path(args.instrument, variant)
        pd.DataFrame([metrics]).to_csv(r_path, index=False)
        log.info("Report → %s", r_path)


if __name__ == "__main__":
    main()
