"""
backtest.py — Walk-forward Wheel strategy backtest on daily equity data.

Uses yfinance OHLCV + Black-Scholes (HV30 × IV_PREMIUM as IV proxy).

Usage:
    PYTHONPATH=. python3 strategies/wheel/backtest.py \\
        --start 2022-01-01 --end 2024-12-31 [--fetch]

Simulation rules:
  - Every Monday: scan for new CSP positions (respects MAX_POSITIONS cap).
  - Daily: reprice open options; close at 50% profit if triggered.
  - At expiry: assign (stock ≤ CSP strike) or expire worthless.
  - After assignment: immediately open a CC on the next trading day.
  - CC called away (stock ≥ CC strike at expiry): shares sold, cycle resets.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import config  # noqa: E402
from strategies.wheel import wheel_config as cfg  # noqa: E402
from strategies.wheel.strategy import (  # noqa: E402
    RISK_FREE_RATE, ExitType, LegType,
    annualized_yield, bs_delta, bs_price,
    commission_cost, find_strike_by_delta,
)
from strategies.wheel.universe import (  # noqa: E402
    compute_hv, compute_iv_rank, fetch_all_ohlcv, load_bars, save_bars,
)

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _iv_at(df: pd.DataFrame, dt: pd.Timestamp) -> float:
    """Compute implied vol (HV30 × IV_PREMIUM) at a given date."""
    hist    = df["close"].loc[:dt]
    hv_ser  = compute_hv(hist)
    hv30    = float(hv_ser.iloc[-1]) if len(hv_ser) and not pd.isna(hv_ser.iloc[-1]) else 0.20
    return max(hv30 * cfg.IV_PREMIUM, 0.05)


def _iv_rank_at(df: pd.DataFrame, dt: pd.Timestamp) -> float:
    hist   = df["close"].loc[:dt]
    hv_ser = compute_hv(hist)
    return compute_iv_rank(hv_ser)


def _next_expiry_ts(from_dt: pd.Timestamp, dte: int = cfg.TARGET_DTE) -> pd.Timestamp:
    """Return next Friday at least `dte` calendar days ahead."""
    target = from_dt + pd.Timedelta(days=dte)
    while target.weekday() != 4:   # Friday
        target += pd.Timedelta(days=1)
    return target.normalize()


# ── Simulation ─────────────────────────────────────────────────────────────────

def _simulate(
    bars:       dict[str, pd.DataFrame],
    start:      datetime,
    end:        datetime,
    initial_eq: float = 100_000.0,
) -> list[dict]:
    """
    Walk-forward simulation. Returns list of completed trade dicts.
    Each trade represents one closed option leg (CSP or CC).
    """

    # Build common sorted daily index
    all_dates: list[pd.Timestamp] = sorted(
        set().union(*[set(df.index) for df in bars.values()])
    )
    tz = all_dates[0].tzinfo if all_dates else None

    def _ts(dt: datetime) -> pd.Timestamp:
        ts = pd.Timestamp(dt)
        if tz is None:
            return ts.tz_localize(None) if ts.tzinfo else ts
        return ts.tz_convert(tz) if ts.tzinfo else ts.tz_localize(tz)

    start_ts = _ts(start)
    end_ts   = _ts(end)
    all_dates = [d for d in all_dates if start_ts <= d <= end_ts]
    if not all_dates:
        log.error("No trading days in range %s–%s", start, end)
        return []

    # Portfolio state
    cash   = initial_eq
    shares: dict[str, dict] = {}   # symbol → {qty, csp_strike, csp_premium}
    open_positions: dict[str, dict] = {}   # symbol → position dict
    trades: list[dict] = []

    # Track pending CC to open (after assignment, open CC on next bar)
    pending_cc: list[str] = []

    def _get_price(sym: str, dt: pd.Timestamp) -> float | None:
        df = bars.get(sym)
        if df is None or dt not in df.index:
            return None
        return float(df.loc[dt, "close"])

    for dt in all_dates:
        # ── 1. Open pending CCs from last bar's assignment ─────────────────────
        for sym in pending_cc[:]:
            price = _get_price(sym, dt)
            if price is None:
                continue
            sh    = shares.get(sym)
            if sh is None:
                pending_cc.remove(sym)
                continue

            iv        = _iv_at(bars[sym], dt)
            T         = cfg.TARGET_DTE / 365.0
            cc_strike = find_strike_by_delta(price, T, RISK_FREE_RATE, iv, cfg.CC_TARGET_DELTA, "call")
            # CC strike must be at or above cost basis
            cost_basis = sh["csp_strike"] - sh["csp_premium"]
            cc_strike  = max(cc_strike, round(cost_basis * 1.005 * 2) / 2)  # at least 0.5% above cost

            cc_prem  = bs_price(price, cc_strike, T, RISK_FREE_RATE, iv, "call")
            cc_prem *= (1.0 - cfg.SLIPPAGE_BPS / 10_000)
            cash    += cc_prem * 100 - commission_cost(1)

            expiry = _next_expiry_ts(dt, cfg.TARGET_DTE)
            open_positions[sym] = {
                "leg":        "cc",
                "strike":     cc_strike,
                "premium_in": cc_prem,
                "iv":         iv,
                "entry_dt":   dt,
                "expiry_dt":  expiry,
                "csp_strike": sh["csp_strike"],
                "csp_premium": sh["csp_premium"],
            }
            pending_cc.remove(sym)

        # ── 2. Update open positions ───────────────────────────────────────────
        for sym in list(open_positions.keys()):
            pos   = open_positions[sym]
            price = _get_price(sym, dt)
            if price is None:
                continue

            iv      = _iv_at(bars[sym], dt)
            days_left = max((pos["expiry_dt"] - dt).days, 0)
            T       = days_left / 365.0
            opt_type = "put" if pos["leg"] == "csp" else "call"
            current_val = bs_price(price, pos["strike"], T, RISK_FREE_RATE, iv, opt_type)

            # ── 50% profit close ──────────────────────────────────────────────
            if current_val <= pos["premium_in"] * (1.0 - cfg.CLOSE_PROFIT_PCT):
                buyback = current_val * (1.0 + cfg.SLIPPAGE_BPS / 10_000)
                pnl     = (pos["premium_in"] - buyback) * 100 - commission_cost(2)
                cash   += pnl  # premium already received at open; pay buyback now
                # Actually at open we did: cash += premium_in × 100 - commission
                # At close we do: cash -= buyback × 100 + commission
                # But let me keep it consistent: record net pnl, adjust cash by (-buyback × 100 - commission)
                cash   -= pnl  # undo the pnl already added above
                cash   -= buyback * 100 + commission_cost(1)   # pay to close

                if pos["leg"] == "cc":
                    # Return collateral tracking: shares stay, no longer have CC
                    pass

                trades.append({
                    "symbol":    sym,
                    "leg":       pos["leg"],
                    "entry_dt":  pos["entry_dt"].date().isoformat(),
                    "exit_dt":   dt.date().isoformat(),
                    "strike":    pos["strike"],
                    "premium_in": round(pos["premium_in"], 4),
                    "premium_out": round(buyback, 4),
                    "shares_pnl": 0.0,
                    "exit_type": ExitType.PROFIT_CLOSE.value,
                    "pnl":       round(pnl, 2),
                })
                del open_positions[sym]

                if pos["leg"] == "cc":
                    # Shares still held; schedule new CC next cycle
                    pending_cc.append(sym)
                continue

            # ── At expiration ──────────────────────────────────────────────────
            if dt >= pos["expiry_dt"]:
                if pos["leg"] == "csp":
                    if price <= pos["strike"]:
                        # Assigned: buy 100 shares at strike
                        cash -= pos["strike"] * 100 + commission_cost(1)
                        shares[sym] = {
                            "qty":         100,
                            "csp_strike":  pos["strike"],
                            "csp_premium": pos["premium_in"],
                        }
                        trades.append({
                            "symbol":    sym,
                            "leg":       "csp",
                            "entry_dt":  pos["entry_dt"].date().isoformat(),
                            "exit_dt":   dt.date().isoformat(),
                            "strike":    pos["strike"],
                            "premium_in": round(pos["premium_in"], 4),
                            "premium_out": 0.0,
                            "shares_pnl": 0.0,
                            "exit_type": ExitType.ASSIGNED.value,
                            "pnl":       round(pos["premium_in"] * 100 - commission_cost(1), 2),
                        })
                        pending_cc.append(sym)
                    else:
                        # Expires worthless — keep full premium
                        trades.append({
                            "symbol":    sym,
                            "leg":       "csp",
                            "entry_dt":  pos["entry_dt"].date().isoformat(),
                            "exit_dt":   dt.date().isoformat(),
                            "strike":    pos["strike"],
                            "premium_in": round(pos["premium_in"], 4),
                            "premium_out": 0.0,
                            "shares_pnl": 0.0,
                            "exit_type": ExitType.EXPIRED_OTM.value,
                            "pnl":       round(pos["premium_in"] * 100 - commission_cost(1), 2),
                        })
                    del open_positions[sym]

                else:  # CC
                    if price >= pos["strike"]:
                        # Called away: sell shares at CC strike
                        sh         = shares.pop(sym, {"csp_strike": pos["csp_strike"], "csp_premium": pos["csp_premium"]})
                        cash      += pos["strike"] * 100 - commission_cost(1)
                        share_pnl  = (pos["strike"] - sh.get("csp_strike", pos["strike"])) * 100
                        # CC premium was already received at entry; this is shares PnL only
                        trades.append({
                            "symbol":    sym,
                            "leg":       "cc",
                            "entry_dt":  pos["entry_dt"].date().isoformat(),
                            "exit_dt":   dt.date().isoformat(),
                            "strike":    pos["strike"],
                            "premium_in": round(pos["premium_in"], 4),
                            "premium_out": 0.0,
                            "shares_pnl": round(share_pnl, 2),
                            "exit_type": ExitType.CALLED_AWAY.value,
                            "pnl":       round(share_pnl - commission_cost(1), 2),
                        })
                    else:
                        # Expires worthless — keep CC premium, still hold shares
                        trades.append({
                            "symbol":    sym,
                            "leg":       "cc",
                            "entry_dt":  pos["entry_dt"].date().isoformat(),
                            "exit_dt":   dt.date().isoformat(),
                            "strike":    pos["strike"],
                            "premium_in": round(pos["premium_in"], 4),
                            "premium_out": 0.0,
                            "shares_pnl": 0.0,
                            "exit_type": ExitType.EXPIRED_OTM.value,
                            "pnl":       round(pos["premium_in"] * 100 - commission_cost(1), 2),
                        })
                        pending_cc.append(sym)
                    del open_positions[sym]

        # ── 3. Open new CSP positions on Mondays ───────────────────────────────
        if dt.weekday() == 0:   # Monday
            n_open = len(open_positions) + len(shares)
            if n_open >= cfg.MAX_POSITIONS:
                continue

            # Rough equity estimate for position sizing
            shares_value = sum(
                (float(bars[s].loc[dt, "close"]) if dt in bars[s].index else 0) * sh["qty"]
                for s, sh in shares.items() if s in bars
            )
            equity_est = cash + shares_value

            for sym, df in bars.items():
                if sym in open_positions or sym in shares:
                    continue
                if len(open_positions) + len(shares) >= cfg.MAX_POSITIONS:
                    break

                price = _get_price(sym, dt)
                if price is None:
                    continue

                # IV rank filter
                iv_rank = _iv_rank_at(df, dt)
                if iv_rank < cfg.MIN_IV_RANK:
                    continue

                iv     = _iv_at(df, dt)
                T      = cfg.TARGET_DTE / 365.0
                strike = find_strike_by_delta(price, T, RISK_FREE_RATE, iv, cfg.CSP_TARGET_DELTA, "put")

                # Position sizing: collateral = strike × 100, capped at MAX_POSITION_PCT
                max_collateral = equity_est * cfg.MAX_POSITION_PCT
                if strike * 100 > max_collateral:
                    continue

                prem   = bs_price(price, strike, T, RISK_FREE_RATE, iv, "put")
                prem  *= (1.0 - cfg.SLIPPAGE_BPS / 10_000)
                ann_y  = annualized_yield(prem, strike, cfg.TARGET_DTE)

                if ann_y < cfg.MIN_ANN_YIELD:
                    continue

                expiry = _next_expiry_ts(dt, cfg.TARGET_DTE)
                cash  += prem * 100 - commission_cost(1)

                open_positions[sym] = {
                    "leg":        "csp",
                    "strike":     strike,
                    "premium_in": prem,
                    "iv":         iv,
                    "entry_dt":   dt,
                    "expiry_dt":  expiry,
                    "csp_strike": strike,
                    "csp_premium": prem,
                }

    # ── 4. Force-close all remaining positions at end of backtest ──────────────
    last_dt = all_dates[-1]
    for sym, pos in list(open_positions.items()):
        price = _get_price(sym, last_dt)
        if price is None:
            continue
        iv       = _iv_at(bars[sym], last_dt)
        opt_type = "put" if pos["leg"] == "csp" else "call"
        val      = bs_price(price, pos["strike"], 1 / 365, RISK_FREE_RATE, iv, opt_type)
        buyback  = val * (1.0 + cfg.SLIPPAGE_BPS / 10_000)
        pnl      = (pos["premium_in"] - buyback) * 100 - commission_cost(2)
        cash    -= buyback * 100 + commission_cost(1)
        trades.append({
            "symbol":     sym,
            "leg":        pos["leg"],
            "entry_dt":   pos["entry_dt"].date().isoformat(),
            "exit_dt":    last_dt.date().isoformat(),
            "strike":     pos["strike"],
            "premium_in": round(pos["premium_in"], 4),
            "premium_out": round(buyback, 4),
            "shares_pnl": 0.0,
            "exit_type":  ExitType.FORCE_CLOSE.value,
            "pnl":        round(pnl, 2),
        })

    for sym, sh in shares.items():
        price = _get_price(sym, last_dt)
        if price is None:
            continue
        share_pnl = (price - sh["csp_strike"]) * sh["qty"]
        cash     += price * sh["qty"]
        trades.append({
            "symbol":     sym,
            "leg":        "shares",
            "entry_dt":   "",
            "exit_dt":    last_dt.date().isoformat(),
            "strike":     sh["csp_strike"],
            "premium_in": sh["csp_premium"],
            "premium_out": 0.0,
            "shares_pnl": round(share_pnl, 2),
            "exit_type":  ExitType.FORCE_CLOSE.value,
            "pnl":        round(share_pnl, 2),
        })

    return trades


# ── Metrics ────────────────────────────────────────────────────────────────────

def _metrics(trades: list[dict], initial_eq: float, start: datetime, end: datetime) -> dict:
    if not trades:
        return {"error": "no trades"}

    df = pd.DataFrame(trades)
    df["exit_dt"] = pd.to_datetime(df["exit_dt"])
    df.sort_values("exit_dt", inplace=True)

    # Running equity: start + cumulative pnl
    total_pnl = df["pnl"].sum()
    # Approximate final equity: initial + all option premiums + shares pnl
    # (cash tracking in simulation may drift slightly; use total-pnl approach for metrics)
    final_eq  = initial_eq + total_pnl
    total_ret = (final_eq - initial_eq) / initial_eq

    daily_pnl = df.groupby(df["exit_dt"].dt.date)["pnl"].sum()
    eq_curve  = initial_eq + daily_pnl.cumsum()
    daily_ret = daily_pnl / initial_eq

    n_days  = max((end - start).days, 1)
    ann_ret = (1 + total_ret) ** (365 / n_days) - 1

    sharpe = (daily_ret.mean() / daily_ret.std(ddof=1) * np.sqrt(252)
              if daily_ret.std(ddof=1) > 0 else 0.0)

    roll_max = eq_curve.cummax()
    mdd      = ((eq_curve - roll_max) / roll_max).min()
    calmar   = ann_ret / abs(mdd) if mdd != 0 else 0.0

    opt_trades = df[df["leg"].isin(["csp", "cc"])]
    winners    = opt_trades[opt_trades["pnl"] > 0]
    losers     = opt_trades[opt_trades["pnl"] < 0]

    csp_trades = df[df["leg"] == "csp"]
    cc_trades  = df[df["leg"] == "cc"]

    return {
        "initial_equity":    initial_eq,
        "final_equity":      round(final_eq, 2),
        "total_return_pct":  round(total_ret * 100, 2),
        "ann_return_pct":    round(ann_ret * 100, 2),
        "sharpe_ratio":      round(sharpe, 3),
        "max_drawdown_pct":  round(mdd * 100, 2),
        "calmar_ratio":      round(calmar, 3),
        "total_trades":      len(opt_trades),
        "csp_trades":        len(csp_trades),
        "cc_trades":         len(cc_trades),
        "win_rate_pct":      round(len(winners) / len(opt_trades) * 100, 1) if len(opt_trades) else 0,
        "avg_win":           round(winners["pnl"].mean(), 2) if len(winners) else 0,
        "avg_loss":          round(losers["pnl"].mean(),  2) if len(losers)  else 0,
        "profit_closes":     int((df["exit_type"] == ExitType.PROFIT_CLOSE.value).sum()),
        "expired_otm":       int((df["exit_type"] == ExitType.EXPIRED_OTM.value).sum()),
        "assignments":       int((df["exit_type"] == ExitType.ASSIGNED.value).sum()),
        "called_away":       int((df["exit_type"] == ExitType.CALLED_AWAY.value).sum()),
        "force_closes":      int((df["exit_type"] == ExitType.FORCE_CLOSE.value).sum()),
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Wheel Strategy backtest")
    ap.add_argument("--start",  default="2022-01-01", help="Start date YYYY-MM-DD")
    ap.add_argument("--end",    default="2024-12-31", help="End date YYYY-MM-DD")
    ap.add_argument("--equity", type=float, default=100_000.0, help="Starting equity")
    ap.add_argument("--fetch",  action="store_true", help="Re-fetch bars from yfinance")
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end   = datetime.strptime(args.end,   "%Y-%m-%d").replace(tzinfo=timezone.utc)

    if args.fetch:
        log.info("Fetching daily bars from yfinance (%s → %s)…", args.start, args.end)
        bars = fetch_all_ohlcv(start=start, end=end + timedelta(days=1))
        save_bars(bars)
    else:
        log.info("Loading cached bars from %s", cfg.bars_dir())
        bars = load_bars()
        if not bars:
            log.error("No cached bars. Run with --fetch first.")
            sys.exit(1)

    log.info("Running simulation on %d symbols…", len(bars))
    trades = _simulate(bars, start, end, initial_eq=args.equity)
    log.info("Trades generated: %d", len(trades))

    metrics = _metrics(trades, args.equity, start, end)

    print("\n" + "=" * 50)
    print("  WHEEL STRATEGY BACKTEST RESULTS")
    print("=" * 50)
    for k, v in metrics.items():
        print(f"  {k:<25} {v}")
    print("=" * 50)

    if trades:
        path = cfg.backtest_trades_path()
        pd.DataFrame(trades).to_csv(path, index=False)
        log.info("Trades → %s", path)

    rep_path = cfg.backtest_report_path()
    pd.DataFrame([metrics]).to_csv(rep_path, index=False)
    log.info("Report → %s", rep_path)


if __name__ == "__main__":
    main()
