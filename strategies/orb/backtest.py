"""
backtest.py — ORB strategy backtest engine.

Reads Parquet files produced by data_fetcher.py from data/bars/.

Per trading day:
  1. Base filters on 14-day daily lookback  (price, avg volume, ATR)
  2. Opening bar RelVol → keep top 20 Stocks in Play
  3. Generate ORB signal  (direction from first candle, entry = OR high/low)
  4. Simulate intraday execution  (entry stop → stop loss or EOD close)
  5. Update capital, record trade

Usage:
    from strategies.orb.data_fetcher import prepare, load_universe
    from strategies.orb.backtest import run_backtest

    prepare("2022-01-01", "2024-12-31", n=800)          # one-time download
    trades, equity, metrics = run_backtest(
        load_universe(), "2022-01-01", "2024-12-31"
    )
"""

import logging
from datetime import date, time as dtime
from typing import Optional

import numpy as np
import pandas as pd
import pytz

import config
from strategies.orb import orb_config
from strategies.orb.strategy import Direction, compute_stop_loss, compute_take_profit
from strategies.orb.data_fetcher import bars_path

logger = logging.getLogger(__name__)
ET    = pytz.timezone("America/New_York")

COMMISSION_PER_SHARE = 0.0035   # IB Pro Tiered (matches the paper)


# ── Data loading from Parquet ─────────────────────────────────────────────────

def _load_daily_bars(symbols: list[str], start: str, end: str) -> pd.DataFrame:
    """
    Read daily Parquet files for all symbols, concat into one flat DataFrame.
    Columns: symbol, timestamp, open, high, low, close, volume, date.
    """
    dfs = []
    for sym in symbols:
        path = bars_path(sym, "1day")
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path)
            df = df.loc[start:end]
            if df.empty:
                continue
            df["symbol"] = sym
            dfs.append(df.reset_index())
        except Exception as e:
            logger.debug("Daily bars load failed for %s: %s", sym, e)

    if not dfs:
        return pd.DataFrame()

    combined = pd.concat(dfs, ignore_index=True)
    combined["timestamp"] = pd.to_datetime(combined["timestamp"], utc=True)
    combined["date"]      = combined["timestamp"].dt.tz_convert(ET).dt.date
    return combined


def _load_opening_bars(symbols: list[str], start: str, end: str) -> pd.DataFrame:
    """
    Read 5-min Parquet files for all symbols, keep only the 9:30 AM ET bar.
    Columns: symbol, timestamp, open, high, low, close, volume, date.
    """
    dfs = []
    for sym in symbols:
        path = bars_path(sym, "5min")
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path)
            df = df.loc[start:end]
            if df.empty:
                continue
            df.index = pd.to_datetime(df.index, utc=True).tz_convert(ET)
            opening = df[df.index.time == dtime(9, 30)].copy()
            if opening.empty:
                continue
            opening["symbol"] = sym
            opening["date"]   = opening.index.date
            dfs.append(opening.reset_index().rename(columns={"index": "timestamp"}))
        except Exception as e:
            logger.debug("Opening bars load failed for %s: %s", sym, e)

    if not dfs:
        return pd.DataFrame()
    return pd.concat(dfs, ignore_index=True)


def _load_intraday_for_date(symbols: list[str], trade_date: date) -> pd.DataFrame:
    """
    Read 5-min Parquet files for specific symbols, return only regular market hours
    bars for trade_date (9:30 AM – 3:55 PM ET, excludes extended hours).
    """
    dfs = []
    date_str = str(trade_date)
    for sym in symbols:
        path = bars_path(sym, "5min")
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path)
            df.index = pd.to_datetime(df.index, utc=True).tz_convert(ET)
            day_bars = df[
                (df.index.date == trade_date) &
                (df.index.time >= dtime(9, 30)) &
                (df.index.time <= dtime(15, 55))   # last regular-hours bar
            ].copy()
            if day_bars.empty:
                continue
            day_bars["symbol"] = sym
            dfs.append(day_bars.reset_index().rename(columns={"index": "timestamp"}))
        except Exception as e:
            logger.debug("Intraday load failed for %s %s: %s", sym, date_str, e)

    if not dfs:
        return pd.DataFrame()
    return pd.concat(dfs, ignore_index=True)


# ── Pre-computations ──────────────────────────────────────────────────────────

def _compute_daily_metrics(daily_df: pd.DataFrame, today: date) -> pd.DataFrame:
    """
    Compute 14-day ATR and avg volume per symbol using data strictly before today.
    Returns only rows passing the base filters.
    """
    lookback = daily_df[daily_df["date"] < today]
    rows = []

    for sym, g in lookback.groupby("symbol"):
        g = g.tail(orb_config.LOOKBACK_DAYS + 1)
        if len(g) < 3:
            continue

        avg_vol    = float(g["volume"].tail(orb_config.LOOKBACK_DAYS).mean())
        last_close = float(g["close"].iloc[-1])
        prev_close = g["close"].shift(1)
        tr = pd.concat([
            g["high"] - g["low"],
            (g["high"] - prev_close).abs(),
            (g["low"]  - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = float(tr.tail(orb_config.LOOKBACK_DAYS).mean())

        if (
            last_close  > orb_config.MIN_PRICE
            and avg_vol >= orb_config.MIN_AVG_VOLUME
            and atr      > orb_config.MIN_ATR
        ):
            rows.append({"symbol": sym, "last_close": last_close, "avg_volume": avg_vol, "atr": atr})

    return pd.DataFrame(rows)


def _compute_avg_orvolume(opening_df: pd.DataFrame, today: date) -> pd.Series:
    """14-day avg of the 9:30 bar volume per symbol, using only days before today."""
    past = opening_df[opening_df["date"] < today]
    return (
        past.sort_values("date")
            .groupby("symbol")["volume"]
            .apply(lambda s: s.tail(orb_config.LOOKBACK_DAYS).mean())
    )


# ── Trade simulation ──────────────────────────────────────────────────────────

def _simulate_trade(
    sym_bars:        pd.DataFrame,
    direction:       Direction,
    entry_price:     float,
    stop_loss:       float,
    atr:             float,
    capital:         float,   # current capital — used for daily cap tracking
    initial_capital: float,   # fixed sizing base so positions don't compound
) -> Optional[dict]:
    """
    Simulate one ORB trade on a full day of 5-min bars.
    Returns a trade dict or None if the entry stop was never triggered.
    """
    bars = sym_bars[sym_bars["timestamp"].dt.time >= dtime(9, 35)].reset_index(drop=True)
    if bars.empty:
        return None

    take_profit = compute_take_profit(entry_price, stop_loss, atr, direction)

    # Find entry trigger
    entry_idx = None
    for i, row in bars.iterrows():
        if direction == Direction.LONG  and row["high"] >= entry_price:
            entry_idx = i; break
        if direction == Direction.SHORT and row["low"]  <= entry_price:
            entry_idx = i; break

    if entry_idx is None:
        return None

    entry_time  = bars.loc[entry_idx, "timestamp"]
    post_entry  = bars.iloc[entry_idx + 1 :].reset_index(drop=True)
    exit_price  = float(bars.iloc[-1]["close"])
    exit_reason = "eod"
    exit_time   = bars.iloc[-1]["timestamp"]

    for _, row in post_entry.iterrows():
        sl_hit = (direction == Direction.LONG  and row["low"]  <= stop_loss) or \
                 (direction == Direction.SHORT and row["high"] >= stop_loss)
        tp_hit = (direction == Direction.LONG  and row["high"] >= take_profit) or \
                 (direction == Direction.SHORT and row["low"]  <= take_profit)

        if sl_hit and tp_hit:
            # Both on same bar — assume stop hit first (conservative)
            exit_price  = stop_loss
            exit_reason = "stop_loss"
            exit_time   = row["timestamp"]
            break
        if sl_hit:
            exit_price  = stop_loss
            exit_reason = "stop_loss"
            exit_time   = row["timestamp"]
            break
        if tp_hit:
            exit_price  = take_profit
            exit_reason = "take_profit"
            exit_time   = row["timestamp"]
            break

    risk_per_share = abs(entry_price - stop_loss)
    if risk_per_share == 0:
        return None

    sign          = 1 if direction == Direction.LONG else -1
    pnl_per_share = sign * (exit_price - entry_price)
    pnl_r         = pnl_per_share / risk_per_share

    shares = min(
        int(initial_capital * orb_config.RISK_PER_TRADE / risk_per_share),
        int(initial_capital * orb_config.MAX_LEVERAGE    / entry_price),
    )
    if shares <= 0:
        return None

    commission  = COMMISSION_PER_SHARE * shares * 2
    pnl_dollars = pnl_per_share * shares - commission

    return {
        "direction":   direction.value,
        "entry_price": round(entry_price,  4),
        "exit_price":  round(exit_price,   4),
        "stop_loss":   round(stop_loss,    4),
        "take_profit": round(take_profit,  4),
        "entry_time":  entry_time,
        "exit_time":   exit_time,
        "exit_reason": exit_reason,
        "pnl_r":       round(pnl_r,        4),
        "pnl_dollars": round(pnl_dollars,  2),
        "shares":      shares,
        "commission":  round(commission,   2),
        "atr":         round(atr,          4),
    }


# ── Main backtest loop ────────────────────────────────────────────────────────

def run_backtest(
    symbols:         list[str],
    start:           str,
    end:             str,
    initial_capital: float = 25_000.0,
) -> tuple[pd.DataFrame, pd.Series, dict]:
    """
    Run the ORB backtest. Returns (trades_df, equity_curve, metrics).
    Prerequisite: run data_fetcher.download_bars(symbols, start, end) first.
    """
    logger.info("Loading daily bars from Parquet...")
    daily_df = _load_daily_bars(symbols, start, end)
    if daily_df.empty:
        raise ValueError("No daily bars found. Run data_fetcher.download_bars() first.")

    logger.info("Loading 5-min opening bars from Parquet...")
    opening_df = _load_opening_bars(symbols, start, end)
    if opening_df.empty:
        raise ValueError("No 5-min bars found. Run data_fetcher.download_bars() first.")

    trading_days = sorted(d for d in daily_df["date"].unique() if start <= str(d) <= end)
    logger.info("Backtesting %d trading days  (%s → %s)", len(trading_days), start, end)

    capital       = initial_capital
    trades:       list[dict]        = []
    equity_curve: dict[date, float] = {}

    for n, today in enumerate(trading_days):
        equity_curve[today] = capital

        # 1. Base filters
        eligible = _compute_daily_metrics(daily_df, today)
        if eligible.empty:
            continue
        eligible_syms = set(eligible["symbol"])

        # 2. Today's opening bars
        today_open = opening_df[
            (opening_df["date"] == today) &
            (opening_df["symbol"].isin(eligible_syms))
        ]
        if today_open.empty:
            continue

        # 3. RelVol
        avg_orv    = _compute_avg_orvolume(opening_df, today)
        candidates = []

        for _, row in today_open.iterrows():
            sym       = row["symbol"]
            today_orv = float(row["volume"])
            avg       = avg_orv.get(sym, 0.0)
            if avg == 0:
                continue
            relvol = today_orv / avg
            if relvol < orb_config.MIN_RELVOL:
                continue

            first_open  = float(row["open"])
            first_close = float(row["close"])
            if   first_close > first_open: direction = Direction.LONG
            elif first_close < first_open: direction = Direction.SHORT
            else: continue

            entry_price = float(row["high"]) if direction == Direction.LONG else float(row["low"])
            meta        = eligible[eligible["symbol"] == sym].iloc[0]
            stop_loss   = compute_stop_loss(entry_price, float(meta["atr"]), direction)

            candidates.append({
                "symbol":      sym,
                "relvol":      relvol,
                "direction":   direction,
                "entry_price": entry_price,
                "stop_loss":   stop_loss,
                "atr":         float(meta["atr"]),
            })

        if not candidates:
            continue

        # 4. Top N by RelVol
        candidates.sort(key=lambda x: x["relvol"], reverse=True)
        top = candidates[:orb_config.TOP_N]

        # 5. Load full intraday bars for top candidates only
        intraday_df = _load_intraday_for_date([c["symbol"] for c in top], today)
        if intraday_df.empty:
            continue

        # 6. Simulate trades (stop adding positions once daily cap is hit)
        day_pnl      = 0.0
        daily_loss_floor =  -orb_config.MAX_DAILY_LOSS_PCT * capital
        daily_gain_ceil  =   orb_config.MAX_DAILY_GAIN_PCT * capital

        for c in top:
            if day_pnl <= daily_loss_floor or day_pnl >= daily_gain_ceil:
                break

            sym_bars = intraday_df[intraday_df["symbol"] == c["symbol"]]
            if sym_bars.empty:
                continue
            trade = _simulate_trade(
                sym_bars        = sym_bars,
                direction       = c["direction"],
                entry_price     = c["entry_price"],
                stop_loss       = c["stop_loss"],
                atr             = c["atr"],
                capital         = capital,
                initial_capital = initial_capital,
            )
            if trade:
                ordered = {"symbol": c["symbol"], "date": today}
                ordered.update(trade)
                ordered["relvol"] = round(c["relvol"], 4)
                trades.append(ordered)
                day_pnl += trade["pnl_dollars"]

        capital += day_pnl

        if n % 50 == 0 or n == len(trading_days) - 1:
            logger.info(
                "Day %d/%d  %s  |  capital $%.0f  |  trades: %d",
                n + 1, len(trading_days), today, capital, len(trades),
            )

    trades_df = pd.DataFrame(trades)
    equity    = pd.Series(equity_curve).rename("capital")
    metrics   = _compute_metrics(trades_df, equity, initial_capital)
    _save_results(trades_df, equity, metrics)
    return trades_df, equity, metrics


# ── Performance metrics ───────────────────────────────────────────────────────

def _compute_metrics(trades_df: pd.DataFrame, equity: pd.Series, initial_capital: float) -> dict:
    if equity.empty:
        return {}

    total_return = (equity.iloc[-1] - initial_capital) / initial_capital
    n_years      = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    cagr         = (equity.iloc[-1] / initial_capital) ** (1 / n_years) - 1
    daily_ret    = equity.pct_change().dropna()
    sharpe       = float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0.0
    mdd          = float(((equity - equity.cummax()) / equity.cummax()).min())

    win_rate  = float((trades_df["pnl_dollars"] > 0).mean()) if not trades_df.empty else 0.0
    avg_pnl_r = float(trades_df["pnl_r"].mean())             if not trades_df.empty else 0.0
    sl_count  = int((trades_df["exit_reason"] == "stop_loss").sum())  if not trades_df.empty else 0
    tp_count  = int((trades_df["exit_reason"] == "take_profit").sum()) if not trades_df.empty else 0
    eod_count = int((trades_df["exit_reason"] == "eod").sum())        if not trades_df.empty else 0

    return {
        "total_return_%":    round(total_return * 100, 2),
        "cagr_%":            round(cagr          * 100, 2),
        "sharpe_ratio":      round(sharpe,              2),
        "max_drawdown_%":    round(mdd            * 100, 2),
        "worst_day_%":       round(float(daily_ret.min()) * 100, 2),
        "win_rate_%":        round(win_rate        * 100, 2),
        "avg_pnl_r":         round(avg_pnl_r,            4),
        "n_trades":          len(trades_df),
        "stop_loss_exits":   sl_count,
        "take_profit_exits": tp_count,
        "eod_exits":         eod_count,
        "final_capital_$":   round(equity.iloc[-1],      2),
        "initial_capital_$": initial_capital,
    }


def _save_results(trades_df: pd.DataFrame, equity: pd.Series, metrics: dict) -> None:
    out = config.OUTPUTS_DIR
    if not trades_df.empty:
        trades_df.to_csv(out / "orb_backtest_trades.csv", index=False)
    equity.to_csv(out / "orb_backtest_equity.csv", header=["capital"])
    pd.Series(metrics).to_csv(out / "orb_backtest_metrics.csv", header=["value"])

    logger.info("── ORB Backtest Results ──────────────────────────")
    for k, v in metrics.items():
        logger.info("  %-25s %s", k, v)
    logger.info("  Results saved to %s", out)


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    from strategies.orb.data_fetcher import load_universe

    start = sys.argv[1] if len(sys.argv) > 1 else "2024-01-01"
    end   = sys.argv[2] if len(sys.argv) > 2 else "2025-12-31"

    logger.info("ORB Backtest  |  %s → %s  |  ATR-tier stops + %s R-multiples",
                start, end, [t["tp_r"] for t in orb_config.ATR_TIERS])

    symbols = load_universe()
    trades_df, equity, metrics = run_backtest(symbols, start, end)

    print("\n── Results ─────────────────────────────────────")
    for k, v in metrics.items():
        print(f"  {k:<25} {v}")
    print(f"\n  Stop-loss exits : {metrics.get('stop_loss_exits', 0)}")
    print(f"  Take-profit exits: {metrics.get('take_profit_exits', 0)}")
    print(f"  EOD exits        : {metrics.get('eod_exits', 0)}")
