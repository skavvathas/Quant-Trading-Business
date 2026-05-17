"""
backtest.py — Vectorized backtester for the Regime-Based Mean Reversion system.

Usage:
    python backtest.py

Outputs:
    - Console: summary metrics + per-symbol breakdown
    - outputs/backtest_report.csv
    - outputs/backtest_trades.csv
    - Four charts (shown interactively if display is available)
"""

import logging
import sys
import warnings
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
import pandas as pd
import yfinance as yf

import config
from data_manager import fetch_vix_history

warnings.filterwarnings("ignore", category=FutureWarning)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backtest")


# ── Data loading ───────────────────────────────────────────────────────────────

def _flatten_yf_df(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    Normalise a yfinance DataFrame regardless of column format.

    yfinance ≥ 0.2.38 returns a MultiIndex (price, ticker); older versions
    return a flat Index.  Both are reduced to lowercase flat columns.
    """
    if isinstance(df.columns, pd.MultiIndex):
        # Drop the ticker level → (Close, SPY) becomes Close
        df = df.xs(symbol, level="Ticker", axis=1)
    df.columns = [c.lower() for c in df.columns]
    return df


def load_price_history(symbols: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    """Download daily OHLCV for each symbol from Yahoo Finance."""
    data = {}
    for sym in symbols:
        logger.info("Downloading %s …", sym)
        try:
            df = yf.download(sym, start=start, end=end, interval="1d",
                             auto_adjust=True, progress=False)
            if df.empty:
                logger.warning("%s: no data returned", sym)
                continue
            df = _flatten_yf_df(df, sym)
            df.index = pd.to_datetime(df.index)
            df.dropna(subset=["close"], inplace=True)
            data[sym] = df
            logger.info("%s: %d rows (%s – %s)",
                        sym, len(df), df.index[0].date(), df.index[-1].date())
        except Exception as e:
            logger.error("%s download failed: %s", sym, e)
    return data


# ── Core signal computation ────────────────────────────────────────────────────

def compute_signals(df: pd.DataFrame, lookback: int = 20) -> pd.DataFrame:
    """
    Add per-row signals to a daily price DataFrame.

    Columns added:
        daily_return, realized_vol, regime, z_score, signal
    """
    out = df.copy()
    closes = out["close"]

    # Daily returns
    out["daily_return"] = closes.pct_change()

    # Realized vol (annualized, 20-day rolling std of returns)
    out["realized_vol"] = (
        out["daily_return"]
        .rolling(lookback)
        .std()
        .mul(np.sqrt(252))
    )

    # Regime
    lo = config.REALIZED_VOL_LOW_THRESHOLD
    hi = config.REALIZED_VOL_HIGH_THRESHOLD
    out["regime"] = np.where(
        out["realized_vol"] < lo, "low_vol",
        np.where(out["realized_vol"] < hi, "medium_vol", "high_vol")
    )

    # Z-score (20-day rolling SMA + STD on daily close)
    sma = closes.rolling(lookback).mean()
    std = closes.rolling(lookback).std(ddof=1)
    out["sma_20"] = sma
    out["std_20"] = std
    out["z_score"] = (closes - sma) / std.replace(0, np.nan)

    # Signal: +1 long, -1 short, 0 neutral  (skip high_vol)
    threshold = config.Z_SCORE_ENTRY_THRESHOLD
    out["signal"] = 0
    active = out["regime"] != "high_vol"
    out.loc[active & (out["z_score"] < -threshold), "signal"] = 1
    out.loc[active & (out["z_score"] > threshold), "signal"] = -1

    return out


# ── Trade simulation ───────────────────────────────────────────────────────────

def simulate_trades(
    df: pd.DataFrame,
    vix_series: pd.Series,
    symbol: str,
    max_hold_days: int = config.MAX_HOLD_DAYS,
    tp_pct: float = config.TAKE_PROFIT_PCT,
    sl_pct: float = config.STOP_LOSS_PCT,
) -> list[dict]:
    """
    Walk forward through signals and simulate individual trade outcomes.

    Entry on next-day open (market-order slippage approximation).
    Exit rules checked on each subsequent close:
        1. Take profit (+2%)
        2. Stop loss   (-1%)
        3. Max hold    (3 days)
    """
    trades = []
    in_trade = False
    entry_idx = None
    entry_price = None
    direction = None
    stock_regime = None
    vix_mult = None

    closes = df["close"].values
    opens = df["open"].values if "open" in df.columns else closes
    dates = df.index

    for i, (date, row) in enumerate(df.iterrows()):
        if in_trade:
            hold_days = i - entry_idx
            current_close = closes[i]
            sign = 1 if direction == 1 else -1
            pnl_pct = sign * (current_close - entry_price) / entry_price

            exit_reason = None
            if pnl_pct >= tp_pct:
                exit_reason = "take_profit"
            elif pnl_pct <= sl_pct:
                exit_reason = "stop_loss"
            elif hold_days >= max_hold_days:
                exit_reason = "max_hold"

            if exit_reason:
                # Approximate vix adjustment on P&L (sizing already baked in)
                base_size = config.BASE_POSITION_SIZE
                stock_mult = config.STOCK_REGIME_MULTIPLIERS.get(stock_regime, 0.0)
                final_size = int(base_size * stock_mult * vix_mult)

                trades.append({
                    "symbol": symbol,
                    "entry_date": dates[entry_idx].date(),
                    "entry_price": round(entry_price, 4),
                    "exit_date": date.date(),
                    "exit_price": round(current_close, 4),
                    "direction": "LONG" if direction == 1 else "SHORT",
                    "pnl_pct": round(pnl_pct * 100, 4),
                    "hold_days": hold_days,
                    "exit_reason": exit_reason,
                    "regime": stock_regime,
                    "shares": final_size,
                    "pnl_dollars": round(pnl_pct * entry_price * final_size, 2),
                    "z_score_entry": round(row.get("z_score", 0) or 0, 4),
                })
                in_trade = False
                entry_idx = None

        if not in_trade and row["signal"] != 0:
            # Enter on next bar open (look-ahead guard: i+1 must exist)
            if i + 1 >= len(df):
                continue
            direction = row["signal"]
            entry_price = float(opens[i + 1])
            entry_idx = i + 1
            stock_regime = row["regime"]

            # VIX on entry date
            vix_on_date = None
            if vix_series is not None and not vix_series.empty:
                try:
                    vix_on_date = float(vix_series.asof(date))
                except Exception:
                    vix_on_date = 20.0
            if vix_on_date is None or np.isnan(vix_on_date):
                vix_on_date = 20.0

            if vix_on_date < config.VIX_LOW_THRESHOLD:
                vix_mult = 1.0
            elif vix_on_date <= config.VIX_HIGH_THRESHOLD:
                vix_mult = 0.8
            else:
                vix_mult = 0.5

            in_trade = True

    return trades


# ── Metrics ────────────────────────────────────────────────────────────────────

def compute_metrics(trades: list[dict], symbol: str, initial_capital: float = 100_000) -> dict:
    """Compute summary performance metrics from a list of trade dicts."""
    if not trades:
        return {"symbol": symbol, "n_trades": 0}

    pnl = [t["pnl_pct"] for t in trades]
    pnl_dollars = [t["pnl_dollars"] for t in trades]
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p <= 0]

    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 1e-9
    profit_factor = gross_profit / gross_loss if gross_loss else float("inf")

    # Daily equity curve
    daily_returns = pd.Series(pnl_dollars) / initial_capital
    cumulative = (1 + daily_returns).cumprod()
    rolling_max = cumulative.cummax()
    drawdown = (cumulative - rolling_max) / rolling_max
    max_drawdown = float(drawdown.min()) * 100

    sharpe = (
        (daily_returns.mean() / daily_returns.std() * np.sqrt(252))
        if daily_returns.std() > 0 else 0.0
    )

    return {
        "symbol": symbol,
        "n_trades": len(trades),
        "win_rate_pct": round(len(wins) / len(pnl) * 100, 2),
        "avg_win_pct": round(np.mean(wins), 4) if wins else 0,
        "avg_loss_pct": round(np.mean(losses), 4) if losses else 0,
        "profit_factor": round(profit_factor, 3),
        "sharpe": round(sharpe, 3),
        "max_drawdown_pct": round(max_drawdown, 2),
        "total_pnl_pct": round(sum(pnl), 2),
        "total_pnl_dollars": round(sum(pnl_dollars), 2),
        "avg_hold_days": round(np.mean([t["hold_days"] for t in trades]), 2),
        "long_trades": sum(1 for t in trades if t["direction"] == "LONG"),
        "short_trades": sum(1 for t in trades if t["direction"] == "SHORT"),
    }


# ── Equity curve ───────────────────────────────────────────────────────────────

def build_equity_curve(
    all_trades: list[dict],
    initial_capital: float = config.BACKTEST_INITIAL_CAPITAL,
) -> pd.Series:
    """Build a daily equity curve from all trades combined."""
    if not all_trades:
        return pd.Series([initial_capital])

    df = pd.DataFrame(all_trades)
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    daily_pnl = df.groupby("exit_date")["pnl_dollars"].sum()
    full_range = pd.date_range(
        start=df["exit_date"].min(),
        end=df["exit_date"].max(),
        freq="B",
    )
    daily_pnl = daily_pnl.reindex(full_range, fill_value=0)
    equity = initial_capital + daily_pnl.cumsum()
    return equity


# ── Charts ─────────────────────────────────────────────────────────────────────

def plot_results(
    all_trades: list[dict],
    symbol_data: dict[str, pd.DataFrame],
    vix_series: pd.Series,
    save_dir: Path = config.OUTPUTS_DIR,
) -> None:
    fig = plt.figure(figsize=(18, 20))
    fig.suptitle("Regime-Based Mean Reversion — Backtest Results", fontsize=16, y=0.98)
    gs = gridspec.GridSpec(4, 2, figure=fig, hspace=0.45, wspace=0.3)

    # ── 1. Cumulative equity curve ─────────────────────────────────────────────
    ax_eq = fig.add_subplot(gs[0, :])
    equity = build_equity_curve(all_trades)
    equity.plot(ax=ax_eq, color="steelblue", linewidth=1.5, label="Portfolio")
    ax_eq.axhline(config.BACKTEST_INITIAL_CAPITAL, color="gray", linestyle="--", alpha=0.5)
    ax_eq.set_title("Cumulative Equity Curve")
    ax_eq.set_ylabel("Portfolio Value ($)")
    ax_eq.legend()
    ax_eq.grid(alpha=0.3)

    # ── 2. Drawdown chart ──────────────────────────────────────────────────────
    ax_dd = fig.add_subplot(gs[1, :])
    if not equity.empty:
        roll_max = equity.cummax()
        drawdown = (equity - roll_max) / roll_max * 100
        drawdown.plot(ax=ax_dd, color="crimson", linewidth=1)
        ax_dd.fill_between(drawdown.index, drawdown.values, 0, alpha=0.3, color="crimson")
        ax_dd.axhline(-config.MAX_DAILY_DRAWDOWN_PCT * 100, color="black",
                      linestyle=":", linewidth=1, label="Kill switch")
    ax_dd.set_title("Portfolio Drawdown (%)")
    ax_dd.set_ylabel("Drawdown (%)")
    ax_dd.legend()
    ax_dd.grid(alpha=0.3)

    # ── 3. Win/loss distribution ───────────────────────────────────────────────
    ax_dist = fig.add_subplot(gs[2, 0])
    if all_trades:
        pnl_vals = [t["pnl_pct"] for t in all_trades]
        colors = ["green" if p > 0 else "crimson" for p in pnl_vals]
        ax_dist.hist(pnl_vals, bins=40, color="steelblue", edgecolor="white", alpha=0.7)
        ax_dist.axvline(0, color="black", linewidth=1)
        ax_dist.set_title("P&L Distribution (%)")
        ax_dist.set_xlabel("P&L (%)")
        ax_dist.set_ylabel("Frequency")
        ax_dist.grid(alpha=0.3)

    # ── 4. Per-symbol trade count / win rate ───────────────────────────────────
    ax_sym = fig.add_subplot(gs[2, 1])
    if all_trades:
        df_t = pd.DataFrame(all_trades)
        sym_stats = df_t.groupby("symbol").apply(
            lambda x: pd.Series({
                "n_trades": len(x),
                "win_rate": (x["pnl_pct"] > 0).mean() * 100,
            })
        )
        x = range(len(sym_stats))
        bars = ax_sym.bar(x, sym_stats["win_rate"], color="steelblue", alpha=0.7)
        ax_sym.set_xticks(list(x))
        ax_sym.set_xticklabels(sym_stats.index)
        ax_sym.axhline(50, color="gray", linestyle="--", alpha=0.5)
        ax_sym.set_title("Win Rate by Symbol (%)")
        ax_sym.set_ylabel("Win Rate (%)")
        ax_sym.grid(alpha=0.3, axis="y")
        for bar, (_, row) in zip(bars, sym_stats.iterrows()):
            ax_sym.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.5,
                f"n={int(row['n_trades'])}",
                ha="center", fontsize=8,
            )

    # ── 5. Z-score timeseries with signals (SPY) ───────────────────────────────
    spy_sym = "SPY" if "SPY" in symbol_data else list(symbol_data.keys())[0]
    ax_z = fig.add_subplot(gs[3, :])
    if spy_sym in symbol_data:
        spy_df = symbol_data[spy_sym]
        spy_df["z_score"].plot(ax=ax_z, color="navy", linewidth=0.8, alpha=0.7, label="Z-score")
        ax_z.axhline(config.Z_SCORE_ENTRY_THRESHOLD, color="crimson",
                     linestyle="--", linewidth=0.8, label=f"+{config.Z_SCORE_ENTRY_THRESHOLD} (short)")
        ax_z.axhline(-config.Z_SCORE_ENTRY_THRESHOLD, color="green",
                     linestyle="--", linewidth=0.8, label=f"-{config.Z_SCORE_ENTRY_THRESHOLD} (long)")
        ax_z.axhline(0, color="gray", linewidth=0.5, alpha=0.5)

        spy_trades = [t for t in all_trades if t["symbol"] == spy_sym]
        for t in spy_trades:
            color = "green" if t["direction"] == "LONG" else "crimson"
            ax_z.axvline(pd.Timestamp(t["entry_date"]), color=color, alpha=0.3, linewidth=0.7)

        ax_z.set_title(f"Z-score Timeseries — {spy_sym} (vertical lines = entries)")
        ax_z.set_ylabel("Z-score")
        ax_z.legend(fontsize=8)
        ax_z.grid(alpha=0.3)

    out_path = save_dir / "backtest_charts.png"
    plt.savefig(out_path, dpi=120, bbox_inches="tight")
    logger.info("Charts saved to %s", out_path)
    try:
        plt.show()
    except Exception:
        pass


# ── Report generation ──────────────────────────────────────────────────────────

def print_report(metrics_list: list[dict], all_trades: list[dict]) -> None:
    sep = "=" * 70
    print(f"\n{sep}")
    print("REGIME-BASED MEAN REVERSION — BACKTEST REPORT")
    print(f"Period : {config.BACKTEST_START} → {config.BACKTEST_END}")
    print(f"Capital : ${config.BACKTEST_INITIAL_CAPITAL:,.0f}")
    print(sep)

    for m in metrics_list:
        if m["n_trades"] == 0:
            print(f"\n{m['symbol']}: no trades generated")
            continue
        print(f"\n{'─'*40}")
        print(f"  Symbol         : {m['symbol']}")
        print(f"  Trades         : {m['n_trades']}  "
              f"(L={m['long_trades']} / S={m['short_trades']})")
        print(f"  Win Rate       : {m['win_rate_pct']:.1f}%")
        print(f"  Avg Win        : +{m['avg_win_pct']:.3f}%")
        print(f"  Avg Loss       :  {m['avg_loss_pct']:.3f}%")
        print(f"  Profit Factor  : {m['profit_factor']:.3f}")
        print(f"  Sharpe         : {m['sharpe']:.3f}")
        print(f"  Max Drawdown   : {m['max_drawdown_pct']:.2f}%")
        print(f"  Total P&L      : {m['total_pnl_dollars']:+,.2f}$  ({m['total_pnl_pct']:+.2f}%)")
        print(f"  Avg Hold       : {m['avg_hold_days']:.1f} days")

    # Portfolio roll-up
    if all_trades:
        combined = compute_metrics(all_trades, "PORTFOLIO")
        print(f"\n{sep}")
        print("PORTFOLIO COMBINED")
        print(f"  Total Trades   : {combined['n_trades']}")
        print(f"  Win Rate       : {combined['win_rate_pct']:.1f}%")
        print(f"  Profit Factor  : {combined['profit_factor']:.3f}")
        print(f"  Sharpe         : {combined['sharpe']:.3f}")
        print(f"  Max Drawdown   : {combined['max_drawdown_pct']:.2f}%")
        print(f"  Total P&L      : ${combined['total_pnl_dollars']:+,.2f}")
    print(sep + "\n")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    symbols = config.SYMBOLS
    start = config.BACKTEST_START
    end = config.BACKTEST_END

    logger.info("Loading price history …")
    raw_data = load_price_history(symbols, start, end)

    logger.info("Loading VIX history …")
    vix = fetch_vix_history(start, end)

    logger.info("Computing signals …")
    signal_data: dict[str, pd.DataFrame] = {}
    for sym, df in raw_data.items():
        signal_data[sym] = compute_signals(df)

    logger.info("Simulating trades …")
    all_trades: list[dict] = []
    metrics_list: list[dict] = []

    for sym in symbols:
        if sym not in signal_data:
            metrics_list.append({"symbol": sym, "n_trades": 0})
            continue
        trades = simulate_trades(signal_data[sym], vix, sym)
        all_trades.extend(trades)
        metrics_list.append(compute_metrics(trades, sym))
        logger.info("%s: %d trades simulated", sym, len(trades))

    # Save trade log
    if all_trades:
        trades_df = pd.DataFrame(all_trades)
        trades_path = config.OUTPUTS_DIR / "backtest_trades.csv"
        trades_df.to_csv(trades_path, index=False)
        logger.info("Trade log saved to %s", trades_path)

    # Save metrics
    if metrics_list:
        report_df = pd.DataFrame(metrics_list)
        report_path = config.OUTPUTS_DIR / "backtest_report.csv"
        report_df.to_csv(report_path, index=False)
        logger.info("Report saved to %s", report_path)

    print_report(metrics_list, all_trades)

    logger.info("Generating charts …")
    if signal_data:
        plot_results(all_trades, signal_data, vix)
    else:
        logger.warning("No price data available — skipping charts")


if __name__ == "__main__":
    main()
