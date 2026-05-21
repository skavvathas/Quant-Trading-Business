"""
run_oos_sweep.py — ATR_STOP_PCT sweep for any date period.

Runs all 4 strategy combinations for both baseline and optimised across
7 ATR values (5%–35%):
  - QQQ  baseline  (single run, ATR not used)
  - QQQ  optimised × 7 ATR values
  - TQQQ baseline  (single run, ATR not used)
  - TQQQ optimised × 7 ATR values

Output file tags:
  period=oos   → baseline: oos_base   | optimised: oos_atr05 … oos_atr35
  period=paper → baseline: paper_base | optimised: paper_atr05 … paper_atr35

Usage:
    # Out-of-sample 2024–2026
    python run_oos_sweep.py
    python run_oos_sweep.py --skip-download

    # Paper period 2016–2023
    python run_oos_sweep.py --period paper --skip-download

    # Custom dates
    python run_oos_sweep.py --period oos --start 2024-01-01 --end 2026-03-31 --skip-download
"""

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

ATR_PCT_LIST = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35]


def main():
    parser = argparse.ArgumentParser(
        description="ATR sweep backtest — runs baseline + optimised (7 ATR values) for QQQ & TQQQ",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Out-of-sample 2024–2026 (default)
  python run_oos_sweep.py --skip-download

  # Paper period 2016–2023
  python run_oos_sweep.py --period paper --skip-download

  # Fetch new data first, then run
  python run_oos_sweep.py --period oos
        """,
    )
    parser.add_argument("--period",        default="oos",
                        choices=["oos", "paper"],
                        help="'oos'   → 2024-01-01 to 2026-03-31  (default)\n"
                             "'paper' → 2016-01-01 to 2023-02-17")
    parser.add_argument("--start",         default=None,
                        help="Override start date (YYYY-MM-DD)")
    parser.add_argument("--end",           default=None,
                        help="Override end date (YYYY-MM-DD)")
    parser.add_argument("--equity",        default=25_000.0, type=float,
                        help="Starting equity in USD (default: 25000)")
    parser.add_argument("--skip-download", action="store_true",
                        help="Use cached bars, skip Alpaca fetch")
    args = parser.parse_args()

    # Period defaults
    if args.period == "paper":
        start = args.start or "2016-01-01"
        end   = args.end   or "2023-02-17"
        period_tag = "paper"
    else:
        start = args.start or "2024-01-01"
        end   = args.end   or "2026-05-15"
        period_tag = "oos"

    from strategies.orb_qqq.data_fetcher import fetch_bars
    from strategies.orb_qqq.backtest import run_backtest
    from strategies.orb_qqq import orb_qqq_config as cfg
    import strategies.orb_qqq.strategy as _strat
    import pandas as pd

    logger.info("=" * 60)
    logger.info("ATR Sweep  |  period=%s  |  %s → %s  |  equity=$%.0f",
                period_tag, start, end, args.equity)
    logger.info("ATR values: %s", ", ".join(f"{p:.0%}" for p in ATR_PCT_LIST))
    logger.info("=" * 60)

    # ── Step 1: data ──────────────────────────────────────────────────────────
    if args.skip_download:
        logger.info("Skipping download — using cached bars")
    else:
        logger.info("Fetching 5-min + daily bars for QQQ + TQQQ  %s → %s …", start, end)
        fetch_bars(["QQQ", "TQQQ"], start, end)

    # ── Step 2: baseline (single run per instrument, ATR not used) ────────────
    logger.info("-" * 60)
    logger.info("BASELINE runs")
    logger.info("-" * 60)
    base_tag = f"{period_tag}_base"
    for symbol in ["TQQQ", "QQQ"]:
        logger.info("  %s BASELINE  tag=%s", symbol, base_tag)
        try:
            trades, _, metrics = run_backtest(
                symbol=symbol, start=start, end=end,
                variant="baseline", capital=args.equity,
            )
        except FileNotFoundError as e:
            logger.error("  %s — run without --skip-download first.", e)
            continue

        if trades:
            pd.DataFrame(trades).to_csv(
                cfg.backtest_trades_path(symbol, "baseline", tag=base_tag), index=False)
        pd.DataFrame([metrics]).to_csv(
            cfg.backtest_report_path(symbol, "baseline", tag=base_tag), index=False)
        logger.info("  ✓ total_return=%.1f%%  sharpe=%.3f  mdd=%.1f%%  win=%.1f%%",
                    metrics.get("total_return_pct", 0),
                    metrics.get("sharpe_ratio",     0),
                    metrics.get("max_drawdown_pct", 0),
                    metrics.get("win_rate_pct",     0))

    # ── Step 3: optimised sweep ───────────────────────────────────────────────
    logger.info("-" * 60)
    logger.info("OPTIMISED sweep  (%d ATR values × 2 instruments = %d runs)",
                len(ATR_PCT_LIST), len(ATR_PCT_LIST) * 2)
    logger.info("-" * 60)
    for pct in ATR_PCT_LIST:
        cfg.ATR_STOP_PCT        = pct
        _strat.cfg.ATR_STOP_PCT = pct
        tag = f"{period_tag}_atr{int(pct * 100):02d}"
        logger.info("  ATR=%.0f%%  tag=%s", pct * 100, tag)

        for symbol in ["TQQQ", "QQQ"]:
            try:
                trades, _, metrics = run_backtest(
                    symbol=symbol, start=start, end=end,
                    variant="optimised", capital=args.equity,
                )
            except FileNotFoundError as e:
                logger.error("  %s — run without --skip-download first.", e)
                continue

            if trades:
                pd.DataFrame(trades).to_csv(
                    cfg.backtest_trades_path(symbol, "optimised", tag=tag), index=False)
            pd.DataFrame([metrics]).to_csv(
                cfg.backtest_report_path(symbol, "optimised", tag=tag), index=False)
            logger.info("    %s  total_return=%.1f%%  sharpe=%.3f  mdd=%.1f%%  win=%.1f%%",
                        symbol,
                        metrics.get("total_return_pct", 0),
                        metrics.get("sharpe_ratio",     0),
                        metrics.get("max_drawdown_pct", 0),
                        metrics.get("win_rate_pct",     0))

    logger.info("=" * 60)
    logger.info("Sweep complete (%s). Refresh the dashboard to see results.", period_tag)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
