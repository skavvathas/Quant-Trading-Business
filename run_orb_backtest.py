"""
run_orb_backtest.py — Entry point for the ORB backtest.

Usage:
    python run_orb_backtest.py                          # default: 2022-01-01 to 2024-12-31
    python run_orb_backtest.py --start 2023-01-01 --end 2024-12-31
    python run_orb_backtest.py --skip-download          # if data is already cached
"""

import argparse
import logging
import sys
from pathlib import Path

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="ORB Strategy Backtest")
    parser.add_argument("--start",         default="2022-01-01", help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end",           default="2024-12-31", help="Backtest end date   (YYYY-MM-DD)")
    parser.add_argument("--capital",       default=25_000.0, type=float, help="Starting capital in USD")
    parser.add_argument("--n-symbols",     default=800,      type=int,   help="Universe size (top N by dollar volume)")
    parser.add_argument("--skip-download",  action="store_true", help="Skip data download, use cached data")
    parser.add_argument("--download-only", action="store_true", help="Only download data, do not run backtest")
    args = parser.parse_args()

    from strategies.orb.data_fetcher import fetch_top_symbols, save_universe, load_universe, download_bars
    from strategies.orb.backtest import run_backtest

    # ── Step 1: universe + data download ──────────────────────────────────────
    if args.skip_download:
        logger.info("Skipping download — loading universe from disk...")
        symbols = load_universe()
    else:
        logger.info("Fetching top %d symbols by dollar volume from Alpaca...", args.n_symbols)
        symbols = fetch_top_symbols(n=args.n_symbols)
        save_universe(symbols)

        logger.info("Downloading daily + 5-min bars (%s → %s)...", args.start, args.end)
        logger.info("This may take a while on first run — data is cached for future runs.")
        download_bars(symbols, args.start, args.end, timeframe="both")

    logger.info("Universe: %d symbols", len(symbols))

    if args.download_only:
        logger.info("Download complete. Run with --skip-download to backtest.")
        return

    # ── Step 2: run backtest ───────────────────────────────────────────────────
    logger.info("Starting backtest  %s → %s  |  capital $%.0f", args.start, args.end, args.capital)
    trades, equity, metrics = run_backtest(
        symbols         = symbols,
        start           = args.start,
        end             = args.end,
        initial_capital = args.capital,
    )

    # ── Step 3: print summary ──────────────────────────────────────────────────
    print("\n" + "="*50)
    print("  ORB BACKTEST RESULTS")
    print("="*50)
    for k, v in metrics.items():
        print(f"  {k:<25} {v}")
    print("="*50)
    print(f"\n  Trades saved  → outputs/orb_backtest_trades.csv")
    print(f"  Equity saved  → outputs/orb_backtest_equity.csv")
    print(f"  Metrics saved → outputs/orb_backtest_metrics.csv\n")


if __name__ == "__main__":
    main()
