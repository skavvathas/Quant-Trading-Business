"""
run_atr_sweep.py — Sweep ATR_STOP_PCT for the TQQQ optimised variant.

Usage:
    python run_atr_sweep.py
"""

import sys
import logging
logging.basicConfig(level=logging.WARNING)

import strategies.orb_qqq.orb_qqq_config as cfg
from strategies.orb_qqq.backtest import run_backtest

SYMBOL   = "TQQQ"
START    = "2024-01-01"
END      = "2026-03-31"
CAPITAL  = 25_000.0
PCT_VALUES = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]

results = []

for pct in PCT_VALUES:
    cfg.ATR_STOP_PCT = pct
    # also patch strategy module which caches the import
    import strategies.orb_qqq.strategy as strat_mod
    strat_mod.cfg.ATR_STOP_PCT = pct

    print(f"Running ATR_STOP_PCT = {pct:.0%} …", flush=True)
    trades, _, metrics = run_backtest(
        symbol=SYMBOL, start=START, end=END,
        variant="optimised", capital=CAPITAL,
    )
    results.append((pct, metrics))

# ── Print comparison table ─────────────────────────────────────────────────────
COL = 11
HDR = ["ATR%", "Total Ret", "Ann Ret", "Sharpe", "MDD", "Win%", "Stops", "EOD exits"]
print("\n" + "  ".join(h.rjust(COL) for h in HDR))
print("  ".join("─" * COL for _ in HDR))

for pct, m in results:
    row = [
        f"{pct:.0%}",
        f"{m.get('total_return_pct', 0):.1f}%",
        f"{m.get('ann_return_pct',   0):.1f}%",
        f"{m.get('sharpe_ratio',     0):.3f}",
        f"{m.get('max_drawdown_pct', 0):.1f}%",
        f"{m.get('win_rate_pct',     0):.1f}%",
        str(int(m.get('stop_exits',  0))),
        str(int(m.get('eod_exits',   0))),
    ]
    print("  ".join(v.rjust(COL) for v in row))

print()
