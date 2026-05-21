"""
run_adaptive_trend_backtest.py — Entry point for the AdaptiveTrend backtest.

Usage:
    python run_adaptive_trend_backtest.py                          # default: 2023-01-01 to 2024-12-31
    python run_adaptive_trend_backtest.py --start 2022-01-01 --end 2024-12-31
    python run_adaptive_trend_backtest.py --skip-download          # if bars are already cached
    python run_adaptive_trend_backtest.py --download-only          # fetch bars, don't run sim
"""

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="AdaptiveTrend Crypto Backtest")
    parser.add_argument("--start",         default="2023-01-01", help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end",           default="2024-12-31", help="Backtest end date   (YYYY-MM-DD)")
    parser.add_argument("--equity",        default=100_000.0, type=float, help="Starting equity in USD")
    parser.add_argument("--skip-download", action="store_true", help="Skip Alpaca fetch, use cached bars")
    parser.add_argument("--download-only", action="store_true", help="Only fetch bars, do not run backtest")
    args = parser.parse_args()

    from datetime import datetime, timezone
    from strategies.adaptive_trend.universe import fetch_all_ohlcv, save_bars, load_bars
    from strategies.adaptive_trend.backtest import _simulate_bar_by_bar, _compute_metrics
    from strategies.adaptive_trend import adaptive_trend_config as cfg
    import pandas as pd

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end   = datetime.strptime(args.end,   "%Y-%m-%d").replace(tzinfo=timezone.utc)

    # ── Step 1: fetch or load bars ─────────────────────────────────────────────
    if args.skip_download:
        logger.info("Skipping download — loading cached bars from %s", cfg.bars_dir())
        bars = load_bars()
        if not bars:
            logger.error("No cached bars found. Run without --skip-download first.")
            sys.exit(1)
    else:
        logger.info("Fetching 6h bars from Alpaca (%s → %s) for %d symbols…",
                    args.start, args.end, len(cfg.UNIVERSE))
        bars = fetch_all_ohlcv(start=start, end=end)
        save_bars(bars)

    logger.info("Bars loaded: %d symbols", len(bars))

    if args.download_only:
        logger.info("Download complete. Run with --skip-download to backtest.")
        return

    # ── Step 2: run simulation ─────────────────────────────────────────────────
    logger.info("Running walk-forward simulation  %s → %s  |  equity $%.0f",
                args.start, args.end, args.equity)
    trades = _simulate_bar_by_bar(bars, start, end, initial_eq=args.equity)
    logger.info("Trades generated: %d", len(trades))

    # ── Step 3: compute + print metrics ───────────────────────────────────────
    metrics = _compute_metrics(trades, args.equity, start, end)

    print("\n" + "=" * 50)
    print("  ADAPTIVE TREND BACKTEST RESULTS")
    print("=" * 50)
    for k, v in metrics.items():
        print(f"  {k:<25} {v}")
    print("=" * 50)

    # ── Step 4: save outputs ───────────────────────────────────────────────────
    if trades:
        trades_path = cfg.backtest_trades_path()
        pd.DataFrame(trades).to_csv(trades_path, index=False)
        logger.info("Trades  → %s", trades_path)

    report_path = cfg.backtest_report_path()
    pd.DataFrame([metrics]).to_csv(report_path, index=False)
    logger.info("Report  → %s", report_path)

    print(f"\n  Trades → outputs/at_backtest_trades.csv")
    print(f"  Report → outputs/at_backtest_report.csv\n")


if __name__ == "__main__":
    main()
