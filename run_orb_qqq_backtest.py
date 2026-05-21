"""
run_orb_qqq_backtest.py — Entry point for the ORB QQQ backtest.

Based on: Zarattini & Aziz, "Can Day Trading Really Be Profitable?" (2023/2025)
Instrument: QQQ (Invesco Nasdaq-100 ETF)

Usage:
    python run_orb_qqq_backtest.py                           # paper period, both variants
    python run_orb_qqq_backtest.py --start 2020-01-01 --end 2024-12-31
    python run_orb_qqq_backtest.py --variants baseline       # baseline only
    python run_orb_qqq_backtest.py --skip-download           # if bars are already cached
    python run_orb_qqq_backtest.py --download-only           # fetch bars, skip simulation
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

INSTRUMENT = "QQQ"


def main():
    parser = argparse.ArgumentParser(description=f"ORB {INSTRUMENT} Backtest")
    parser.add_argument("--start",         default="2016-01-01",
                        help="Backtest start date (YYYY-MM-DD) — paper uses 2016-01-01")
    parser.add_argument("--end",           default="2023-02-17",
                        help="Backtest end date (YYYY-MM-DD)   — paper uses 2023-02-17")
    parser.add_argument("--equity",        default=25_000.0, type=float,
                        help="Starting equity in USD (paper: $25,000)")
    parser.add_argument("--variants",      default="both",
                        choices=["baseline", "optimised", "both"],
                        help="baseline: candle stop + 10R target  |  "
                             "optimised: 5%% ATR stop + EOD  |  both: run both")
    parser.add_argument("--skip-download", action="store_true",
                        help="Skip Alpaca data fetch, use cached bars")
    parser.add_argument("--download-only", action="store_true",
                        help="Only fetch bars, do not run the backtest")
    parser.add_argument("--tag",           default="",
                        help="Output file suffix to avoid overwriting other periods "
                             "(e.g. --tag oos → orbqqq_qqq_baseline_oos_report.csv)")
    parser.add_argument("--atr-stop-pct", default=None, type=float,
                        help="Override ATR_STOP_PCT for the optimised variant "
                             "(default: 0.05 paper value; use 0.10 for 2024-2026)")
    args = parser.parse_args()

    from strategies.orb_qqq.data_fetcher import fetch_bars
    from strategies.orb_qqq.backtest import run_backtest
    from strategies.orb_qqq import orb_qqq_config as cfg
    import strategies.orb_qqq.strategy as _strat
    import pandas as pd

    if args.atr_stop_pct is not None:
        cfg.ATR_STOP_PCT      = args.atr_stop_pct
        _strat.cfg.ATR_STOP_PCT = args.atr_stop_pct
        logger.info("ATR_STOP_PCT overridden to %.0f%%", args.atr_stop_pct * 100)

    # ── Step 1: data ──────────────────────────────────────────────────────────
    if args.skip_download:
        logger.info("Skipping download — using cached bars from %s", cfg.bars_dir())
    else:
        logger.info("Fetching 5-min + daily bars for %s (%s → %s)…",
                    INSTRUMENT, args.start, args.end)
        fetch_bars([INSTRUMENT], args.start, args.end)

    if args.download_only:
        logger.info("Download complete. Run without --download-only to backtest.")
        return

    # ── Step 2: run variant(s) ────────────────────────────────────────────────
    variants = ["baseline", "optimised"] if args.variants == "both" else [args.variants]

    for variant in variants:
        logger.info("Running %s %s backtest  %s → %s  |  equity $%.0f",
                    INSTRUMENT, variant.upper(), args.start, args.end, args.equity)
        try:
            trades, equity_curve, metrics = run_backtest(
                symbol  = INSTRUMENT,
                start   = args.start,
                end     = args.end,
                variant = variant,
                capital = args.equity,
            )
        except FileNotFoundError as e:
            logger.error("%s — run without --skip-download to fetch data first.", e)
            continue

        # ── Print ──────────────────────────────────────────────────────────────
        print(f"\n{'='*52}")
        print(f"  ORB {INSTRUMENT} — {variant.upper()}")
        print(f"{'='*52}")
        for k, v in metrics.items():
            print(f"  {k:<25} {v}")
        print(f"{'='*52}")

        # ── Save ───────────────────────────────────────────────────────────────
        if trades:
            t_path = cfg.backtest_trades_path(INSTRUMENT, variant, tag=args.tag)
            pd.DataFrame(trades).to_csv(t_path, index=False)
            logger.info("Trades → %s", t_path)

        r_path = cfg.backtest_report_path(INSTRUMENT, variant, tag=args.tag)
        pd.DataFrame([metrics]).to_csv(r_path, index=False)
        logger.info("Report → %s", r_path)

    print()


if __name__ == "__main__":
    main()
