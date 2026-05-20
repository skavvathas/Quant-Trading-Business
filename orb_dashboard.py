"""
orb_dashboard.py — ORB strategy live monitor.

Standalone:  streamlit run orb_dashboard.py
Via hub:     streamlit run app.py  →  click ORB card
"""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import pytz

import config
from strategies.orb import orb_config

ET           = pytz.timezone("America/New_York")
REFRESH      = 15
ORB_PID_FILE = config.DATA_DIR / "orb_main.pid"

_CSS = """
<style>
[data-testid="stAppViewContainer"] { background: #0f0f1a; }
[data-testid="stHeader"]           { background: transparent; }
.section-title {
    font-size: 0.70rem; font-weight: 700; letter-spacing: 0.12em;
    text-transform: uppercase; color: #6b7280;
    margin-bottom: 10px; padding-bottom: 6px;
    border-bottom: 1px solid #1f2937;
}
.kpi-box {
    background: #13131f; border: 1px solid #1f2937;
    border-radius: 10px; padding: 14px 18px; text-align: center;
}
.kpi-label { font-size: 0.70rem; color: #6b7280; text-transform: uppercase; letter-spacing: 0.08em; }
.kpi-value { font-size: 1.7rem; font-weight: 700; margin: 4px 0 0; }
.kpi-sub   { font-size: 0.68rem; color: #6b7280; margin-top: 4px; }
.kpi-green { color: #4ade80; }
.kpi-red   { color: #f87171; }
.kpi-white { color: #f3f4f6; }
.kpi-yellow{ color: #facc15; }
.tag-long    { background:#14532d; color:#86efac; border-radius:4px; padding:2px 8px; font-size:0.72rem; font-weight:700; }
.tag-short   { background:#7f1d1d; color:#fca5a5; border-radius:4px; padding:2px 8px; font-size:0.72rem; font-weight:700; }
.tag-filled  { background:#1e3a5f; color:#93c5fd; border-radius:4px; padding:2px 8px; font-size:0.70rem; }
.tag-pending { background:#3b2f00; color:#fde68a; border-radius:4px; padding:2px 8px; font-size:0.70rem; }
.tag-none    { background:#1f2937; color:#6b7280; border-radius:4px; padding:2px 8px; font-size:0.70rem; }
.pnl-pos { color: #4ade80; font-weight: 600; }
.pnl-neg { color: #f87171; font-weight: 600; }
</style>
"""


# ── Trading process helpers ────────────────────────────────────────────────────

def _is_trading_active() -> bool:
    if not ORB_PID_FILE.exists():
        return False
    try:
        pid = int(ORB_PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, ValueError, OSError):
        ORB_PID_FILE.unlink(missing_ok=True)
        return False


def _start_trading() -> tuple[bool, str]:
    try:
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).parent / "orb_main.py")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        ORB_PID_FILE.write_text(str(proc.pid))
        return True, (
            f"Trading loop started (PID {proc.pid}). "
            "It will build the watchlist at 8:00 AM, scan at 9:35 AM, "
            "then sync every 60 s and close all positions at 3:50 PM ET."
        )
    except Exception as e:
        return False, f"Failed to start trading loop: {e}"


def _stop_trading() -> tuple[bool, str]:
    if not ORB_PID_FILE.exists():
        return False, "Trading loop was not running (no PID file found)."
    try:
        pid = int(ORB_PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        ORB_PID_FILE.unlink(missing_ok=True)
        return True, (
            f"Trading loop stopped (PID {pid}). "
            "Note: open positions are NOT automatically closed — "
            "check Alpaca to manage them manually if needed."
        )
    except Exception as e:
        ORB_PID_FILE.unlink(missing_ok=True)
        return False, f"SIGTERM failed: {e}. PID file removed."


def _restart_trading() -> tuple[bool, str]:
    """Stop the running loop (if any) then immediately start a fresh one."""
    if ORB_PID_FILE.exists():
        _stop_trading()
    return _start_trading()


def _emergency_stop() -> tuple[bool, str]:
    """Cancel all open orders, close all positions, and kill the trading loop."""
    parts: list[str] = []
    ok = True

    if ORB_PID_FILE.exists():
        loop_ok, loop_msg = _stop_trading()
        parts.append(loop_msg)
        if not loop_ok:
            ok = False

    if config.ALPACA_API_KEY:
        try:
            from alpaca.trading.client import TradingClient
            client = TradingClient(
                api_key=config.ALPACA_API_KEY,
                secret_key=config.ALPACA_SECRET_KEY,
                paper=config.PAPER_TRADING,
            )
            client.cancel_orders()
            client.close_all_positions(cancel_orders=True)
            parts.append("All orders cancelled and positions closed via Alpaca.")
        except Exception as e:
            ok = False
            parts.append(f"Alpaca error: {e}")
    else:
        parts.append("Simulation mode — no live orders to cancel.")

    return ok, " | ".join(parts)


@st.dialog("⚠️ Confirm Emergency Stop")
def _emergency_stop_dialog() -> None:
    st.warning(
        "This will **immediately cancel ALL open orders**, "
        "**close ALL positions** at market price, and **stop the trading loop**."
    )
    st.markdown("This action cannot be undone. Are you sure?")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Yes, terminate everything", type="primary", use_container_width=True):
            with st.spinner("Cancelling orders and closing positions…"):
                ok, msg = _emergency_stop()
            st.session_state["_flash"] = {"level": "success" if ok else "error", "msg": msg}
            st.rerun()
    with col2:
        if st.button("Cancel", use_container_width=True):
            st.rerun()


# ── Dashboard scan ─────────────────────────────────────────────────────────────

def _run_dashboard_scan() -> tuple[str, str]:
    """Returns (level, message) — level is 'success' | 'warning' | 'error'."""
    from strategies.orb.universe import load_watchlist
    from strategies.orb.scanner import fetch_opening_candles, build_setups
    from strategies.orb.strategy import (
        compute_shares, compute_relative_volume, get_direction,
        generate_signal, select_top_n, Direction,
    )

    try:
        watchlist = load_watchlist()
    except FileNotFoundError:
        return "error", (
            "Watchlist not found on disk. "
            "Run build_watchlist() pre-market to generate it first."
        )

    n_watch = len(watchlist)
    symbols = [e.ticker for e in watchlist]

    # Fetch candles once — reused for both ORB signals and RelVol ranking
    opening_candles = fetch_opening_candles(symbols)
    setups = build_setups(watchlist, opening_candles)

    if not setups:
        return "warning", (
            f"Scan checked {n_watch} watchlist symbols but got no candle data. "
            "The opening candle may not be available yet — try again after 9:35 AM ET."
        )

    # ORB signals (full filter pipeline)
    raw_signals = [generate_signal(s, min_relvol=orb_config.MIN_RELVOL) for s in setups]
    signals = select_top_n([s for s in raw_signals if s], n=orb_config.TOP_N)
    signal_tickers = {s.ticker for s in signals}

    # Top 20 by RelVol — ALL setups, no ORB filter applied
    all_rv = []
    for setup in setups:
        rv  = compute_relative_volume(setup)
        direction = get_direction(setup)
        dir_label = "long" if direction == Direction.LONG else "short" if direction == Direction.SHORT else "doji"
        all_rv.append({
            "symbol":    setup.ticker,
            "relvol":    round(rv, 2),
            "direction": dir_label,
            "open":      round(setup.first_candle_open,  2),
            "high":      round(setup.first_candle_high,  2),
            "low":       round(setup.first_candle_low,   2),
            "close":     round(setup.first_candle_close, 2),
            "volume":    int(setup.first_candle_volume),
            "atr":       round(setup.atr_14d, 4),
            "has_signal": setup.ticker in signal_tickers,
        })
    all_rv.sort(key=lambda x: x["relvol"], reverse=True)

    acct   = _alpaca_account()
    equity = acct.get("equity") or 25_000.0

    state = {
        "updated_at": datetime.now(tz=ET).isoformat(),
        "account":    acct,
        "signals": [
            {
                "symbol":      s.ticker,
                "direction":   s.direction.value,
                "entry_price": round(s.entry_price, 4),
                "stop_loss":   round(s.stop_loss,   4),
                "relvol":      round(s.relative_volume, 2),
                "atr":         round(s.atr, 4),
                "shares":      compute_shares(s.entry_price, s.stop_loss, equity),
                "take_profit": round(s.take_profit, 4),
                "status":      "not_triggered",
            }
            for s in signals
        ],
        "top_relvol":    all_rv[:20],
        "open_positions": [],
    }
    # Write to a dedicated file — never overwritten by orb_main.py
    (config.DATA_DIR / "orb_scan_result.json").write_text(json.dumps(state, indent=2))
    return "success", (
        f"Scan complete — {len(signals)} ORB signals, "
        f"{len(all_rv)} symbols with candle data ranked by RelVol. Tables updated below."
    )


def _run_dashboard_scan_30min() -> tuple[str, str]:
    """Returns (level, message) for the 30-min ORB scan."""
    from strategies.orb.universe import load_watchlist
    from strategies.orb.scanner import run_scan_30min
    from strategies.orb.strategy import compute_shares

    try:
        watchlist = load_watchlist()
    except FileNotFoundError:
        return "error", (
            "Watchlist not found. Run build_watchlist() pre-market first."
        )

    n_watch = len(watchlist)
    signals = run_scan_30min(watchlist)

    if not signals:
        return "warning", (
            f"30-min scan checked {n_watch} watchlist symbols but found no qualifying signals. "
            "Make sure it's after 10:00 AM ET so the first 30-min candle has closed."
        )

    acct   = _alpaca_account()
    equity = acct.get("equity") or 25_000.0

    state = {
        "updated_at": datetime.now(tz=ET).isoformat(),
        "signals": [
            {
                "symbol":             s.ticker,
                "direction":          s.direction.value,
                "entry_price":        round(s.entry_price, 4),
                "stop_loss":          round(s.stop_loss, 4),
                "relvol":             round(s.relative_volume, 2),
                "atr":                round(s.atr, 4),
                "shares":             compute_shares(s.entry_price, s.stop_loss, equity),
                "take_profit":        round(s.take_profit, 4),
                "opening_range_high": round(s.opening_range_high, 4),
                "opening_range_low":  round(s.opening_range_low, 4),
                "status":             "not_triggered",
            }
            for s in signals
        ],
    }
    (config.DATA_DIR / "orb_state_30min.json").write_text(json.dumps(state, indent=2))
    return "success", (
        f"30-min scan complete — {len(signals)} signals from {n_watch} watchlist symbols, "
        "ranked by Relative Volume."
    )


# ── Log tail + phase detection ─────────────────────────────────────────────────

@st.cache_data(ttl=5)
def _log_tail(n: int = 40) -> list[str]:
    path = config.LOGS_DIR / "orb_main.log"
    if not path.exists():
        return []
    try:
        return path.read_text().splitlines()[-n:]
    except Exception:
        return []


def _parse_phase(lines: list[str]) -> tuple[str, str, str]:
    """Return (icon, label, color) for the current trading phase."""
    text = "\n".join(lines)

    # Use the most recent heartbeat as the primary status when available
    for line in reversed(lines):
        if "♥ heartbeat" in line and "|" in line:
            detail = line.split("|", 1)[-1].strip()
            if "market hours" in detail:
                return "📈", f"Running — {detail}", "#4ade80"
            if "waiting for 9:35" in detail:
                return "⏳", f"Running — {detail}", "#93c5fd"
            if "day complete" in detail:
                return "🌙", f"Running — {detail}", "#6b7280"
            if "building watchlist" in detail:
                return "⚙️", f"Running — {detail}", "#facc15"
            return "✅", f"Running — {detail}", "#4ade80"

    if "Day complete" in text:
        return "🌙", "Day complete — EOD close finished", "#6b7280"
    if "EOD close" in text:
        return "🔒", "EOD close running — closing all positions…", "#facc15"
    if "ORB scan complete" in text:
        return "✅", "Scan complete — signals live, syncing every 60 s", "#4ade80"
    if "9:35 AM scan" in text:
        return "🔍", "Running 9:35 AM scan…", "#facc15"
    if "Watchlist built" in text or ("Watchlist loaded" in text and "0 symbols" not in text):
        return "⏳", "Watchlist ready — waiting for 9:35 AM scan", "#93c5fd"
    if "Fetching 5-min OR bars" in text or "ORVolume" in text:
        return "⚙️", "Building watchlist — fetching opening-range bars…", "#facc15"
    if "Base filters" in text:
        return "⚙️", "Building watchlist — applying price / volume / ATR filters…", "#facc15"
    if "Fetching daily bars" in text:
        return "⚙️", "Building watchlist — fetching 14-day daily bars…", "#facc15"
    if "Alpaca universe" in text:
        return "⚙️", "Building watchlist — fetching Alpaca universe…", "#facc15"
    if "Pre-market: rebuilding" in text:
        return "⚙️", "Pre-market: rebuilding watchlist…", "#facc15"
    if "ORB live loop starting" in text:
        return "🚀", "Trading loop started — pre-market build will run at 8:00 AM ET", "#93c5fd"
    return "💤", "Idle — no active trading session", "#6b7280"


# ── Data loaders (cached at module level so cache survives st.rerun) ───────────

@st.cache_data(ttl=REFRESH)
def _alpaca_account() -> dict:
    try:
        from alpaca.trading.client import TradingClient
        acc = TradingClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
            paper=config.PAPER_TRADING,
        ).get_account()
        equity      = float(acc.equity)
        last_equity = float(acc.last_equity)
        today_pl    = equity - last_equity
        return {
            "equity":       equity,
            "last_equity":  last_equity,
            "buying_power": float(acc.buying_power),
            "today_pl":     today_pl,
            "today_pl_pct": today_pl / last_equity * 100 if last_equity else 0.0,
        }
    except Exception:
        return {}


@st.cache_data(ttl=REFRESH)
def _alpaca_positions() -> list[dict]:
    try:
        from alpaca.trading.client import TradingClient
        positions = TradingClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
            paper=config.PAPER_TRADING,
        ).get_all_positions()
        return [
            {
                "symbol":          pos.symbol,
                "side":            pos.side.value,
                "qty":             int(float(pos.qty)),
                "entry_price":     float(pos.avg_entry_price),
                "current_price":   float(pos.current_price or 0),
                "unrealized_pl":   float(pos.unrealized_pl or 0),
                "unrealized_plpc": float(pos.unrealized_plpc or 0) * 100,
                "market_value":    float(pos.market_value or 0),
            }
            for pos in positions
        ]
    except Exception:
        return []


@st.cache_data(ttl=5)
def _orb_state() -> dict:
    """Live state written by orb_main.py — account, open positions, 9:35 AM signals."""
    path = config.DATA_DIR / "orb_state.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


@st.cache_data(ttl=5)
def _orb_scan_result() -> dict:
    """Manual scan result written by the dashboard — never overwritten by orb_main.py."""
    path = config.DATA_DIR / "orb_scan_result.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


@st.cache_data(ttl=5)
def _orb_state_30min() -> dict:
    path = config.DATA_DIR / "orb_state_30min.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


@st.cache_data(ttl=10)
def _orb_trades() -> pd.DataFrame:
    path = orb_config.trades_csv()
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


# ── 30-min trade queue ────────────────────────────────────────────────────────

QUEUE_30MIN = config.DATA_DIR / "orb_30min_queue.json"


def _queue_30min_trade(signals: list) -> tuple[bool, str]:
    """Write signals to the queue file — the live loop picks them up on next sync."""
    if not signals:
        return False, "No 30-min signals to trade — run the scan first."
    queue = {
        "queued_at": datetime.now(tz=ET).isoformat(),
        "signals":   signals,
    }
    QUEUE_30MIN.write_text(json.dumps(queue, indent=2))
    return True, (
        f"{len(signals)} 30-min signal(s) queued. "
        "The trading loop will submit stop-entry orders on its next sync (within 60 s)."
    )


@st.dialog("Confirm 30-min Trade")
def _confirm_30min_trade_dialog(signals: list) -> None:
    st.markdown(
        f"Submit **stop-entry orders for {len(signals)} signal(s)** from the 30-min scan? "
        "The live trading loop will pick them up on its next sync and manage fills, "
        "stop-losses, and EOD close automatically."
    )
    if not _is_trading_active():
        st.warning("Trading loop is not running — start it first so it can process the queue.")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Yes, trade these signals", type="primary", use_container_width=True):
            ok, msg = _queue_30min_trade(signals)
            st.session_state["_flash"] = {"level": "success" if ok else "error", "msg": msg}
            st.rerun()
    with col2:
        if st.button("Cancel", use_container_width=True):
            st.rerun()


# ── Shared signals table ───────────────────────────────────────────────────────

def _signals_table_html(signals: list, account_equity: float = 25_000.0) -> str:
    header_cols = ["Symbol", "Direction", "RelVol", "Entry", "Stop", "Target", "Risk/Share", "Shares", "Capital", "Leverage", "Status"]
    hdr = "".join(
        f'<th style="text-align:left;padding:8px 12px;color:#9ca3af;'
        f'font-size:0.70rem;font-weight:600;letter-spacing:0.06em;'
        f'border-bottom:1px solid #1f2937;">{h}</th>'
        for h in header_cols
    )
    body = ""
    for s in signals:
        status   = s.get("status", "not_triggered")
        tag_cls  = {"filled": "tag-filled", "pending": "tag-pending"}.get(status, "tag-none")
        tag_lbl  = {"filled": "Filled",     "pending": "Pending"    }.get(status, "Not triggered")
        dir_cls  = "tag-long" if s["direction"] == "long" else "tag-short"
        dir_lbl  = "↑ LONG"  if s["direction"] == "long" else "↓ SHORT"
        risk     = abs(s["entry_price"] - s["stop_loss"])
        shares    = s.get("shares", 0)
        tp        = s.get("take_profit")
        tp_str    = f"${tp:.2f}" if tp else "—"
        capital   = s["entry_price"] * shares if isinstance(shares, (int, float)) and shares else None
        cap_str   = f"${capital:,.0f}" if capital else "—"
        lev       = capital / account_equity if capital and account_equity else None
        lev_color = "#f87171" if lev and lev > 2 else "#facc15" if lev and lev > 1 else "#4ade80"
        lev_str   = f"{lev:.2f}×" if lev else "—"
        body += f"""
        <tr style="border-bottom:1px solid #1a1a2e;">
          <td style="padding:8px 12px;color:#f3f4f6;font-weight:600;">{s['symbol']}</td>
          <td style="padding:8px 12px;"><span class="{dir_cls}">{dir_lbl}</span></td>
          <td style="padding:8px 12px;color:#facc15;font-weight:600;">{s['relvol']:.1f}×</td>
          <td style="padding:8px 12px;color:#d1d5db;">${s['entry_price']:.2f}</td>
          <td style="padding:8px 12px;color:#f87171;">${s['stop_loss']:.2f}</td>
          <td style="padding:8px 12px;color:#4ade80;">{tp_str}</td>
          <td style="padding:8px 12px;color:#d1d5db;">${risk:.2f}</td>
          <td style="padding:8px 12px;color:#d1d5db;">{shares}</td>
          <td style="padding:8px 12px;color:#93c5fd;font-weight:600;">{cap_str}</td>
          <td style="padding:8px 12px;color:{lev_color};font-weight:600;">{lev_str}</td>
          <td style="padding:8px 12px;"><span class="{tag_cls}">{tag_lbl}</span></td>
        </tr>"""
    return f"""
    <div style="background:#13131f;border:1px solid #1f2937;border-radius:10px;overflow:hidden;">
      <table style="width:100%;border-collapse:collapse;">
        <thead><tr style="background:#0f0f1a;">{hdr}</tr></thead>
        <tbody>{body}</tbody>
      </table>
    </div>"""


# ── Main render ────────────────────────────────────────────────────────────────

def render() -> None:
    """Render the full ORB dashboard. Called both standalone and from pages/."""

    st.set_page_config(
        page_title="ORB (Opening Range Breakout) Dashboard",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.markdown(_CSS, unsafe_allow_html=True)

    # ── Load data ──────────────────────────────────────────────────────────────

    now_et    = datetime.now(tz=ET)
    today_str = now_et.strftime("%Y-%m-%d")

    account     = _alpaca_account()
    positions   = _alpaca_positions()
    state       = _orb_state()        # live state from orb_main.py
    scan_result = _orb_scan_result()  # dashboard manual scan — separate file, never overwritten
    state_30m   = _orb_state_30min()
    trades_df   = _orb_trades()

    # Signals: prefer the manual scan result (stable), fall back to live state
    _scan_signals  = scan_result.get("signals", [])
    _live_signals  = state.get("signals", [])
    signals_source = scan_result if _scan_signals else state

    equity       = account.get("equity",       0.0)
    today_pl     = account.get("today_pl",     0.0)
    today_pl_pct = account.get("today_pl_pct", 0.0)

    today_closed = pd.DataFrame()
    if not trades_df.empty and "timestamp" in trades_df.columns:
        today_closed = trades_df[trades_df["timestamp"].astype(str).str.startswith(today_str)]

    # ── Header ─────────────────────────────────────────────────────────────────

    mkt_open = (
        now_et.weekday() < 5
        and now_et.time() >= dtime(9, 30)
        and now_et.time() <  dtime(16, 0)
    )
    mkt_color = "#4ade80" if mkt_open  else "#f87171"
    mkt_label = "MARKET OPEN" if mkt_open else "MARKET CLOSED"

    scan_time  = state.get("updated_at", "")
    scan_label = f"Last scan: {scan_time[11:19]} ET" if scan_time else "No scan yet"

    trading_active     = _is_trading_active()
    trade_status_color = "#4ade80" if trading_active else "#6b7280"
    trade_status_label = "TRADING LIVE" if trading_active else "TRADING OFF"

    st.markdown(
        f"""
        <div style="display:flex;justify-content:space-between;align-items:center;
                    margin-bottom:14px;padding-bottom:12px;border-bottom:1px solid #1f2937;">
            <span style="font-size:1.2rem;font-weight:700;color:#f3f4f6;">
                ORB Trading Dashboard
                <span style="font-size:0.80rem;font-weight:400;color:#6b7280;margin-left:6px;">
                    (Opening Range Breakout)
                </span>
            </span>
            <span style="display:flex;gap:20px;align-items:center;">
                <span style="font-size:0.78rem;color:#6b7280;">{scan_label}</span>
                <span style="color:{trade_status_color};font-size:0.80rem;font-weight:700;">
                    ● {trade_status_label}
                </span>
                <span style="color:{mkt_color};font-size:0.80rem;font-weight:700;">● {mkt_label}</span>
                <span style="font-size:0.78rem;color:#6b7280;">{now_et.strftime('%H:%M:%S ET')}</span>
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Terminology legend ─────────────────────────────────────────────────────

    st.markdown("""
    <div style="display:flex;flex-direction:column;gap:6px;margin-bottom:16px;padding:12px 16px;
                background:#13131f;border:1px solid #1f2937;border-radius:8px;">
      <div style="font-size:0.74rem;color:#9ca3af;">
        <b style="color:#facc15;">ATR</b> — Average True Range (14-day). Measures a stock's typical daily dollar move. Drives stop size &amp; position sizing.
      </div>
      <div style="font-size:0.74rem;color:#9ca3af;">
        <b style="color:#facc15;">RelVol</b> — Relative Volume. Today's opening 5-min volume ÷ 14-day average. <b style="color:#f3f4f6;">&gt;1×</b> = unusual activity.
      </div>
      <div style="font-size:0.74rem;color:#9ca3af;">
        <b style="color:#facc15;">ORB Signal</b> — Opening Range Breakout. Entry stop placed at the first candle's high (long ↑) or low (short ↓).
      </div>
      <div style="font-size:0.74rem;color:#9ca3af;">
        <b style="color:#facc15;">Stocks in Play</b> — Top stocks by RelVol that pass all ORB filters (price &gt; $5, avg vol &gt; 1M, ATR &gt; $0.50, non-doji).
      </div>
      <div style="font-size:0.74rem;color:#9ca3af;">
        <b style="color:#facc15;">R</b> — Risk unit. 1R = dollars risked on the trade (stop distance × shares). 2R win means you made twice what you risked. Target is <b style="color:#f3f4f6;">3–4R</b>.
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Controls ───────────────────────────────────────────────────────────────

    ctrl_c1, ctrl_c2, ctrl_c3, ctrl_c4, _ = st.columns([2, 2, 2, 2, 2], gap="small")

    _t             = now_et.time()
    _is_weekend    = now_et.weekday() >= 5
    _after_market  = _is_weekend or _t >= dtime(16, 0)
    _pre_watchlist = _t < dtime(8, 0)
    _pre_open      = dtime(8, 0) <= _t < dtime(9, 30)
    _mins_to_open  = max(0, (9 * 60 + 30) - (_t.hour * 60 + _t.minute))

    with ctrl_c1:
        if trading_active:
            if st.button("⏹  Stop Trading", use_container_width=True):
                ok, msg = _stop_trading()
                st.session_state["_flash"] = {"level": "success" if ok else "error", "msg": msg}
                st.rerun()
        else:
            if st.button("▶  Start Trading", type="primary",
                         disabled=_after_market, use_container_width=True):
                ok, msg = _start_trading()
                st.session_state["_flash"] = {"level": "success" if ok else "error", "msg": msg}
                st.rerun()

            if _after_market:
                _next = "Monday" if _is_weekend and now_et.weekday() == 6 else \
                        "next Monday" if _is_weekend else "tomorrow"
                st.caption(f"Market closed — start {_next} before 8:00 AM ET.")
            elif _pre_watchlist:
                st.caption(f"Start after 8:00 AM ET — watchlist rebuilds then ({_mins_to_open} min to open).")
            elif _pre_open:
                st.caption(f"Ideal time to start — market opens in {_mins_to_open} min.")

    with ctrl_c2:
        after_open    = _t >= dtime(9, 30)
        scan_disabled = not after_open or not config.ALPACA_API_KEY
        scan_help = (
            "Needs API key in .env" if not config.ALPACA_API_KEY
            else "Available after 9:30 AM ET" if not after_open
            else "Fetch live opening candles and rank by RelVol"
        )
        if st.button("🔍  Run Scan Now", disabled=scan_disabled,
                     help=scan_help, use_container_width=True):
            with st.spinner("Fetching opening candles and ranking by RelVol…"):
                level, msg = _run_dashboard_scan()
            st.session_state["_flash"] = {"level": level, "msg": msg}
            _orb_scan_result.clear()
            st.rerun()

    with ctrl_c3:
        if st.button("🔄  Restart Trading Loop",
                     help="Stop the current loop and start a fresh one",
                     use_container_width=True):
            with st.spinner("Restarting trading loop…"):
                ok, msg = _restart_trading()
            st.session_state["_flash"] = {"level": "success" if ok else "error", "msg": msg}
            _log_tail.clear()
            st.rerun()

    with ctrl_c4:
        if st.button(
            "⛔  Emergency Stop",
            use_container_width=True,
            help="Cancel all orders, close all positions, and halt the trading loop",
            type="secondary",
        ):
            _emergency_stop_dialog()

    if "_flash" in st.session_state:
        flash = st.session_state.pop("_flash")
        _disp = {"success": st.success, "warning": st.warning, "error": st.error, "info": st.info}
        _disp.get(flash["level"], st.info)(flash["msg"])

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Trading status panel ───────────────────────────────────────────────────

    log_lines  = _log_tail()
    icon, phase_label, phase_color = _parse_phase(log_lines) if trading_active or log_lines else ("💤", "Idle — start the trading loop to begin", "#6b7280")

    # Filter to INFO/WARNING/ERROR lines for display
    display_lines = [
        l for l in log_lines
        if any(lvl in l for lvl in ("INFO", "WARNING", "ERROR"))
    ][-8:]

    log_html = "".join(
        f'<div style="color:{"#f87171" if "ERROR" in l else "#facc15" if "WARNING" in l else "#9ca3af"};'
        f'font-size:0.72rem;font-family:monospace;padding:1px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
        f'{l}</div>'
        for l in display_lines
    ) or '<div style="color:#4b5563;font-size:0.72rem;font-family:monospace;">No log entries yet.</div>'

    st.markdown(f"""
    <div style="background:#13131f;border:1px solid #1f2937;border-radius:10px;padding:14px 18px;margin-bottom:16px;">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">
        <span style="font-size:1.1rem;">{icon}</span>
        <span style="color:{phase_color};font-weight:700;font-size:0.82rem;">{phase_label}</span>
        <span style="margin-left:auto;font-size:0.68rem;color:#4b5563;">logs/orb_main.log · last {len(display_lines)} lines</span>
      </div>
      <div style="border-top:1px solid #1f2937;padding-top:8px;">{log_html}</div>
    </div>
    """, unsafe_allow_html=True)

    # ── KPI row ────────────────────────────────────────────────────────────────

    k1, k2, k3, k4 = st.columns(4, gap="small")

    pnl_color      = "kpi-green" if today_pl >= 0 else "kpi-red"
    pnl_pct_str    = f"{today_pl_pct:+.2f}%"
    n_open         = len(positions)
    today_wins     = int((today_closed["exit_reason"] == "eod").sum()) if not today_closed.empty else 0
    today_trades_n = len(today_closed)
    win_rate_str   = f"{today_wins/today_trades_n*100:.0f}%" if today_trades_n else "—"

    # All-time closed-trade P&L
    _all_pnl: float = 0.0
    if not trades_df.empty:
        if "pnl_dollars" in trades_df.columns:
            _all_pnl = float(trades_df["pnl_dollars"].astype(float).sum())
        elif all(c in trades_df.columns for c in ["entry_price", "exit_price", "qty", "direction"]):
            def _row_pnl(r):
                try:
                    sign = 1 if str(r.get("direction", "")).lower() == "long" else -1
                    return sign * (float(r["exit_price"]) - float(r["entry_price"])) * int(float(r["qty"]))
                except Exception:
                    return 0.0
            _all_pnl = float(trades_df.apply(_row_pnl, axis=1).sum())
    all_pnl_color = "kpi-green" if _all_pnl >= 0 else "kpi-red"
    all_pnl_pct   = _all_pnl / equity * 100 if equity else 0.0

    # Total unrealized P&L across all open positions
    total_unr     = sum(p["unrealized_pl"] for p in positions)
    unr_color     = "kpi-green" if total_unr >= 0 else "kpi-red"
    buying_power  = account.get("buying_power", 0.0)

    with k1:
        eq_str = f"${equity:,.0f}" if equity else "—"
        st.markdown(f"""<div class="kpi-box">
            <div class="kpi-label">Account Equity</div>
            <div class="kpi-value kpi-white">{eq_str}</div>
            <div class="kpi-sub">Alpaca paper</div>
        </div>""", unsafe_allow_html=True)

    with k2:
        pl_str = f"${today_pl:+,.2f}" if account else "—"
        st.markdown(f"""<div class="kpi-box">
            <div class="kpi-label">Today's P&L</div>
            <div class="kpi-value {pnl_color}">{pl_str}</div>
            <div class="kpi-sub">{pnl_pct_str}</div>
        </div>""", unsafe_allow_html=True)

    with k3:
        st.markdown(f"""<div class="kpi-box">
            <div class="kpi-label">Open Positions</div>
            <div class="kpi-value kpi-white">{n_open}</div>
            <div class="kpi-sub">in market now</div>
        </div>""", unsafe_allow_html=True)

    with k4:
        st.markdown(f"""<div class="kpi-box">
            <div class="kpi-label">Closed Today</div>
            <div class="kpi-value kpi-white">{today_trades_n}</div>
            <div class="kpi-sub">win rate {win_rate_str}</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<div style='margin-top:8px;'></div>", unsafe_allow_html=True)
    k5, k6, k7 = st.columns(3, gap="small")

    with k5:
        signals_n = len(state.get("signals", []))
        st.markdown(f"""<div class="kpi-box">
            <div class="kpi-label">Stocks In Play</div>
            <div class="kpi-value kpi-white">{signals_n}</div>
            <div class="kpi-sub">top by RelVol today</div>
        </div>""", unsafe_allow_html=True)

    with k6:
        all_pnl_str = f"${_all_pnl:+,.2f}" if not trades_df.empty else "—"
        all_pnl_pct_str = f"{all_pnl_pct:+.2f}% of equity" if equity and not trades_df.empty else "all closed trades"
        st.markdown(f"""<div class="kpi-box">
            <div class="kpi-label">Total P&L</div>
            <div class="kpi-value {all_pnl_color}">{all_pnl_str}</div>
            <div class="kpi-sub">{all_pnl_pct_str}</div>
        </div>""", unsafe_allow_html=True)

    with k7:
        unr_str = f"${total_unr:+,.2f}" if positions else "—"
        bp_str  = f"${buying_power:,.0f} buying power" if buying_power else "no open positions"
        st.markdown(f"""<div class="kpi-box">
            <div class="kpi-label">Unrealized Cash</div>
            <div class="kpi-value {unr_color if positions else 'kpi-white'}">{unr_str}</div>
            <div class="kpi-sub">{bp_str}</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Top 20 Stocks in Play ──────────────────────────────────────────────────

    _scan_ts   = scan_result.get("updated_at", "")
    _live_ts   = state.get("updated_at", "")
    _src_label = f"manual scan {_scan_ts[11:19]} ET" if _scan_signals else (f"live loop {_live_ts[11:19]} ET" if _live_signals else "")
    _src_badge = f' <span style="font-size:0.65rem;color:#6b7280;font-weight:400;">({_src_label})</span>' if _src_label else ""

    st.markdown(
        f'<p class="section-title">Top 20 Stocks in Play — Today\'s ORB Candidates{_src_badge}</p>',
        unsafe_allow_html=True,
    )

    signals = signals_source.get("signals", [])

    if signals:
        st.markdown(_signals_table_html(signals, equity), unsafe_allow_html=True)
    else:
        msg = (
            "Click <b>Run Scan Now</b> after 9:30 AM ET to fetch today's top RelVol stocks."
            if config.ALPACA_API_KEY
            else "Add <code>ALPACA_API_KEY</code> to <code>.env</code> then click Run Scan Now after 9:30 AM."
        )
        st.markdown(
            f'<div style="background:#13131f;border:1px solid #1f2937;border-radius:10px;'
            f'padding:20px;color:#6b7280;font-size:0.85rem;">{msg}</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── 30-min Stocks in Play ──────────────────────────────────────────────────

    scan30_hdr, scan30_btn, trade30_btn = st.columns([5, 2, 2], gap="small")
    with scan30_hdr:
        scan30_time  = state_30m.get("updated_at", "")
        scan30_label = f"— last scan {scan30_time[11:19]} ET" if scan30_time else ""
        st.markdown(
            f'<p class="section-title">Top 20 Stocks in Play — 30-min ORB {scan30_label}</p>',
            unsafe_allow_html=True,
        )
    with scan30_btn:
        after_10    = _t >= dtime(10, 0)
        scan30_help = (
            "Available after 10:00 AM ET — first 30-min candle must be closed"
            if not after_10
            else "Fetch the 9:30–10:00 candle and rank by Relative Volume"
        )
        if st.button("🔍  Run 30-min Scan", disabled=not after_10,
                     help=scan30_help, use_container_width=True):
            with st.spinner("Fetching 30-min opening candles…"):
                level, msg = _run_dashboard_scan_30min()
            st.session_state["_flash"] = {"level": level, "msg": msg}
            _orb_state_30min.clear()
            st.rerun()

    signals_30m = state_30m.get("signals", [])

    with trade30_btn:
        queued_already = QUEUE_30MIN.exists()
        trade30_label  = "✅  Queued" if queued_already else "▶  Trade 30-min"
        trade30_help   = (
            "Orders already queued — waiting for trading loop to pick them up"
            if queued_already
            else "Submit stop-entry orders for these signals via the live trading loop"
            if signals_30m
            else "Run the 30-min scan first to generate signals"
        )
        if st.button(trade30_label, disabled=not signals_30m or queued_already,
                     help=trade30_help, use_container_width=True, type="primary"):
            _confirm_30min_trade_dialog(signals_30m)

    if signals_30m:
        st.markdown(_signals_table_html(signals_30m, equity), unsafe_allow_html=True)
    else:
        st.markdown(
            '<div style="background:#13131f;border:1px solid #1f2937;border-radius:10px;'
            'padding:20px;color:#6b7280;font-size:0.85rem;">'
            'Click <b>Run 30-min Scan</b> after 10:00 AM ET to fetch the 30-min opening range candidates.</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── ATR Tier Reference ─────────────────────────────────────────────────────

    _tier_examples = ["TSLA, NVDA", "AAPL, MSFT", "mid-cap", "IMVT, low-ATR"]
    _tier_rows = ""
    tiers = orb_config.ATR_TIERS
    for i, tier in enumerate(tiers):
        next_min  = tiers[i + 1]["atr_min"] if i + 1 < len(tiers) else None
        if next_min is not None:
            range_str = f"${next_min:.0f} – ${tier['atr_min']:.0f}" if next_min > 0 else f"&lt; ${tier['atr_min']:.0f}"
            range_str = f"${tier['atr_min']:.0f} – ${tiers[i-1]['atr_min']:.0f}" if i > 0 else f"≥ ${tier['atr_min']:.0f}"
        else:
            range_str = f"&lt; ${tiers[i-1]['atr_min']:.0f}" if i > 0 else f"≥ ${tier['atr_min']:.0f}"
        # recompute cleanly
        if i == 0:
            range_str = f"≥ ${tier['atr_min']:.0f}"
        elif i == len(tiers) - 1:
            range_str = f"&lt; ${tiers[i-1]['atr_min']:.0f}"
        else:
            range_str = f"${tier['atr_min']:.0f} – ${tiers[i-1]['atr_min']:.0f}"

        stop_str = (
            f"${tier['stop_value']:.2f} fixed"
            if tier["stop_is_fixed"]
            else f"{tier['stop_value']:.2f}× ATR"
        )
        # example $ stop for a mid-point ATR
        mid_atr = tier["atr_min"] + (
            (tiers[i - 1]["atr_min"] - tier["atr_min"]) / 2 if i > 0 else 5.0
        )
        stop_dist = tier["stop_value"] if tier["stop_is_fixed"] else tier["stop_value"] * mid_atr
        risk_pct  = orb_config.RISK_PER_TRADE * 100
        capital   = account.get("equity") or 25_000.0
        example_risk   = capital * orb_config.RISK_PER_TRADE
        example_shares = int(example_risk / stop_dist) if stop_dist else 0
        example_tp_dist = stop_dist * tier["tp_r"]
        example_win    = example_shares * example_tp_dist
        tp_r = tier["tp_r"]
        examples = _tier_examples[i] if i < len(_tier_examples) else ""

        stop_color = "#f87171"
        tp_color   = "#4ade80"
        _tier_rows += f"""
        <tr style="border-bottom:1px solid #1a1a2e;">
          <td style="padding:8px 14px;color:#f3f4f6;font-weight:700;font-size:0.82rem;">ATR {range_str}</td>
          <td style="padding:8px 14px;color:{stop_color};font-size:0.80rem;">{stop_str}</td>
          <td style="padding:8px 14px;color:{tp_color};font-weight:700;font-size:0.80rem;">{tp_r:.1f}R</td>
          <td style="padding:8px 14px;color:#d1d5db;font-size:0.80rem;">
            ≈ ${stop_dist:.2f} stop → {example_shares} shares → win <b style="color:#4ade80;">+${example_win:,.0f}</b> / loss <span style="color:#f87171;">−${example_risk:,.0f}</span>
          </td>
          <td style="padding:8px 14px;color:#6b7280;font-size:0.75rem;font-style:italic;">{examples}</td>
        </tr>"""

    _tier_hdr_cols = ["ATR Range", "Stop Loss", "TP Target", f"Example ({risk_pct:.0f}% risk = ${example_risk:,.0f})", "Stocks"]
    _tier_hdr = "".join(
        f'<th style="text-align:left;padding:8px 14px;color:#9ca3af;font-size:0.70rem;'
        f'font-weight:600;letter-spacing:0.06em;border-bottom:1px solid #1f2937;">{h}</th>'
        for h in _tier_hdr_cols
    )

    with st.expander("⚙️  ATR Tier Reference — Stop-Loss & Take-Profit Rules", expanded=False):
        st.markdown(f"""
        <div style="background:#0f0f1a;border-radius:8px;overflow:hidden;margin-top:4px;">
          <table style="width:100%;border-collapse:collapse;">
            <thead><tr style="background:#13131f;">{_tier_hdr}</tr></thead>
            <tbody>{_tier_rows}</tbody>
          </table>
        </div>
        <div style="margin-top:10px;font-size:0.72rem;color:#6b7280;line-height:1.7;">
          <b style="color:#9ca3af;">How it works:</b>
          Each trade risks <b style="color:#facc15;">{risk_pct:.0f}% of equity (${example_risk:,.0f})</b> — shares = risk ÷ stop distance.
          Stop fires when price crosses the stop level. Take-profit = entry ± (stop distance × R-multiple).
          Max leverage is <b style="color:#93c5fd;">{orb_config.MAX_LEVERAGE:.0f}×</b> (FINRA day-trading limit).
          Daily kill-switch at <b style="color:#f87171;">−{orb_config.MAX_DAILY_LOSS_PCT*100:.0f}%</b> or
          <b style="color:#4ade80;">+{orb_config.MAX_DAILY_GAIN_PCT*100:.0f}%</b> of account.
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Open Positions | Today's Closed Trades ─────────────────────────────────

    left_col, right_col = st.columns(2, gap="large")

    with left_col:
        st.markdown('<p class="section-title">Open Positions (live from Alpaca)</p>',
                    unsafe_allow_html=True)

        state_open = {p["symbol"]: p for p in state.get("open_positions", [])}

        if positions:
            pos_header_cols = ["Symbol", "Side", "Qty", "Entry", "Current", "Stop", "Unreal P&L", "P&L %", "P&L R"]
            pos_hdr_html = "".join(
                f'<th style="text-align:left;padding:8px 12px;color:#9ca3af;'
                f'font-size:0.70rem;font-weight:600;letter-spacing:0.06em;'
                f'border-bottom:1px solid #1f2937;">{h}</th>'
                for h in pos_header_cols
            )
            pos_rows_html = ""
            for p in positions:
                sym   = p["symbol"]
                sl    = state_open.get(sym, {}).get("stop_loss", 0.0)
                entry = p["entry_price"]
                curr  = p["current_price"]
                upl   = p["unrealized_pl"]
                uplpc = p["unrealized_plpc"]
                risk  = abs(entry - sl) if sl else 0
                pnl_r = (curr - entry) / risk if risk and p["side"] == "long" else \
                        (entry - curr) / risk if risk else 0
                pnl_cls   = "pnl-pos" if upl >= 0 else "pnl-neg"
                side_cls  = "tag-long" if p["side"] == "long" else "tag-short"
                side_lbl  = "↑ LONG"  if p["side"] == "long" else "↓ SHORT"
                pos_rows_html += f"""
                <tr style="border-bottom:1px solid #1a1a2e;">
                  <td style="padding:8px 12px;color:#f3f4f6;font-weight:600;">{sym}</td>
                  <td style="padding:8px 12px;"><span class="{side_cls}">{side_lbl}</span></td>
                  <td style="padding:8px 12px;color:#d1d5db;">{p['qty']}</td>
                  <td style="padding:8px 12px;color:#d1d5db;">${entry:.2f}</td>
                  <td style="padding:8px 12px;color:#d1d5db;">${curr:.2f}</td>
                  <td style="padding:8px 12px;color:#d1d5db;">{"$" + f"{sl:.2f}" if sl else "—"}</td>
                  <td style="padding:8px 12px;" class="{pnl_cls}">${upl:+,.2f}</td>
                  <td style="padding:8px 12px;" class="{pnl_cls}">{uplpc:+.2f}%</td>
                  <td style="padding:8px 12px;color:#d1d5db;">{pnl_r:+.2f}R</td>
                </tr>"""
            st.markdown(f"""
            <div style="background:#13131f;border:1px solid #1f2937;border-radius:10px;overflow:auto;">
              <table style="width:100%;border-collapse:collapse;">
                <thead><tr style="background:#0f0f1a;">{pos_hdr_html}</tr></thead>
                <tbody>{pos_rows_html}</tbody>
              </table>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown(
                '<div style="color:#4b5563;font-size:0.82rem;padding:12px 0;">'
                'No open positions.</div>',
                unsafe_allow_html=True,
            )

    with right_col:
        st.markdown('<p class="section-title">Today\'s Closed Trades</p>', unsafe_allow_html=True)

        if not today_closed.empty:
            show_cols = [c for c in [
                "ticker", "direction", "entry_price", "exit_price",
                "pnl_r", "exit_reason", "hold_minutes",
            ] if c in today_closed.columns]
            disp = today_closed[show_cols].copy()
            if "pnl_r" in disp.columns:
                disp["pnl_r"] = disp["pnl_r"].apply(lambda x: f"{float(x):+.2f}R")
            if "hold_minutes" in disp.columns:
                disp["hold_minutes"] = disp["hold_minutes"].apply(
                    lambda x: f"{float(x):.0f}m" if pd.notna(x) else "—"
                )
            st.dataframe(disp, use_container_width=True, hide_index=True)
        else:
            st.markdown(
                '<div style="color:#4b5563;font-size:0.82rem;padding:12px 0;">'
                'No closed trades today.</div>',
                unsafe_allow_html=True,
            )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── All-time P&L curve ─────────────────────────────────────────────────────

    st.markdown('<p class="section-title">All-time Closed-Trade P&L</p>', unsafe_allow_html=True)

    if not trades_df.empty and "timestamp" in trades_df.columns:
        trades_df["timestamp"] = pd.to_datetime(trades_df["timestamp"], errors="coerce")

        if "pnl_dollars" not in trades_df.columns and all(
            c in trades_df.columns for c in ["entry_price", "exit_price", "qty", "direction"]
        ):
            def _calc_pnl(row):
                sign = 1 if str(row.get("direction", "")).lower() == "long" else -1
                return sign * (float(row["exit_price"]) - float(row["entry_price"])) * int(float(row["qty"]))
            trades_df["pnl_dollars"] = trades_df.apply(_calc_pnl, axis=1)

        if "pnl_dollars" in trades_df.columns:
            trades_df = trades_df.sort_values("timestamp")
            trades_df["cum_pnl"] = trades_df["pnl_dollars"].astype(float).cumsum()

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=trades_df["timestamp"],
                y=trades_df["cum_pnl"].round(2),
                mode="lines",
                fill="tozeroy",
                line=dict(color="#22c55e", width=1.8),
                fillcolor="rgba(34,197,94,0.08)",
                name="Cumulative P&L",
            ))
            fig.add_hline(y=0, line_color="#374151", line_width=1)
            fig.update_layout(
                paper_bgcolor="#13131f", plot_bgcolor="#0f0f1a",
                font_color="#9ca3af",
                yaxis=dict(title="Cumulative P&L ($)", gridcolor="#1f2937", tickprefix="$"),
                xaxis=dict(gridcolor="#1f2937"),
                height=260,
                margin=dict(t=10, b=30, l=60, r=20),
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No P&L data to chart yet.")
    else:
        st.caption("No trade history yet. P&L chart will appear after first closed trade.")

    # ── Top 20 by RelVol (market-wide, no ORB filter) ─────────────────────────

    st.markdown('<p class="section-title">Top 20 by Relative Volume — All Watchlist Stocks</p>',
                unsafe_allow_html=True)

    top_rv = scan_result.get("top_relvol", [])

    if top_rv:
        rv_hdr_cols = ["#", "Symbol", "RelVol", "Candle", "OR High", "OR Low", "Volume", "ATR", "ORB Signal"]
        rv_hdr = "".join(
            f'<th style="text-align:left;padding:8px 12px;color:#9ca3af;font-size:0.70rem;'
            f'font-weight:600;letter-spacing:0.06em;border-bottom:1px solid #1f2937;">{h}</th>'
            for h in rv_hdr_cols
        )
        rv_rows = ""
        for rank, row in enumerate(top_rv, 1):
            dir_cls = "tag-long" if row["direction"] == "long" else "tag-short" if row["direction"] == "short" else "tag-none"
            dir_lbl = "↑ Bull" if row["direction"] == "long" else "↓ Bear" if row["direction"] == "short" else "= Doji"
            sig_html = (
                '<span style="background:#14532d;color:#86efac;border-radius:4px;'
                'padding:2px 8px;font-size:0.70rem;font-weight:700;">ORB ✓</span>'
                if row["has_signal"] else
                '<span style="color:#4b5563;font-size:0.70rem;">—</span>'
            )
            vol_str = f"{row['volume']:,}"
            rv_rows += f"""
            <tr style="border-bottom:1px solid #1a1a2e;">
              <td style="padding:8px 12px;color:#4b5563;font-size:0.75rem;">{rank}</td>
              <td style="padding:8px 12px;color:#f3f4f6;font-weight:700;">{row['symbol']}</td>
              <td style="padding:8px 12px;color:#facc15;font-weight:700;font-size:0.90rem;">{row['relvol']:.1f}×</td>
              <td style="padding:8px 12px;"><span class="{dir_cls}">{dir_lbl}</span></td>
              <td style="padding:8px 12px;color:#d1d5db;">${row['high']:.2f}</td>
              <td style="padding:8px 12px;color:#d1d5db;">${row['low']:.2f}</td>
              <td style="padding:8px 12px;color:#9ca3af;font-size:0.78rem;">{vol_str}</td>
              <td style="padding:8px 12px;color:#9ca3af;font-size:0.78rem;">${row['atr']:.2f}</td>
              <td style="padding:8px 12px;">{sig_html}</td>
            </tr>"""
        st.markdown(f"""
        <div style="background:#13131f;border:1px solid #1f2937;border-radius:10px;overflow:hidden;">
          <table style="width:100%;border-collapse:collapse;">
            <thead><tr style="background:#0f0f1a;">{rv_hdr}</tr></thead>
            <tbody>{rv_rows}</tbody>
          </table>
        </div>
        <div style="margin-top:8px;font-size:0.72rem;color:#6b7280;">
          All watchlist symbols with an opening candle, ranked by Relative Volume (today's OR volume ÷ 14-day avg OR volume).
          <b style="color:#9ca3af;">ORB ✓</b> = passed all ORB filters and appears in the Stocks in Play table above.
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown(
            '<div style="background:#13131f;border:1px solid #1f2937;border-radius:10px;'
            'padding:20px;color:#6b7280;font-size:0.85rem;">'
            'Run <b>Run Scan Now</b> after 9:30 AM ET — this table populates from the same scan.</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Footer ─────────────────────────────────────────────────────────────────

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        f'<div style="text-align:center;font-size:0.68rem;color:#374151;">'
        f'Auto-refreshes every {REFRESH}s · {now_et.strftime("%Y-%m-%d %H:%M:%S ET")}</div>',
        unsafe_allow_html=True,
    )

    time.sleep(REFRESH)
    st.rerun()


# ── Standalone entry point ─────────────────────────────────────────────────────
render()
