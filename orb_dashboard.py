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


# ── Dashboard scan ─────────────────────────────────────────────────────────────

def _run_dashboard_scan() -> tuple[str, str]:
    """Returns (level, message) — level is 'success' | 'warning' | 'error'."""
    from strategies.orb.universe import load_watchlist
    from strategies.orb.scanner import run_scan
    from strategies.orb.strategy import compute_shares

    try:
        watchlist = load_watchlist()
    except FileNotFoundError:
        return "error", (
            "Watchlist not found on disk. "
            "Run build_watchlist() pre-market to generate it first."
        )

    n_watch = len(watchlist)
    signals = run_scan(watchlist)

    if not signals:
        return "warning", (
            f"Scan checked {n_watch} watchlist symbols but found no qualifying signals. "
            "The opening candle may not be available yet — try again after 9:35 AM ET."
        )

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
                "status":      "not_triggered",
            }
            for s in signals
        ],
        "open_positions": [],
    }
    (config.DATA_DIR / "orb_state.json").write_text(json.dumps(state, indent=2))
    return "success", (
        f"Scan complete — {len(signals)} signals from {n_watch} watchlist symbols, "
        "ranked by Relative Volume. Table updated below."
    )


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
    path = config.DATA_DIR / "orb_state.json"
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

    account   = _alpaca_account()
    positions = _alpaca_positions()
    state     = _orb_state()
    trades_df = _orb_trades()

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

    # ── Controls ───────────────────────────────────────────────────────────────

    ctrl_c1, ctrl_c2, ctrl_c3 = st.columns([2, 2, 6], gap="small")

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
            _orb_state.clear()
            st.rerun()

    if "_flash" in st.session_state:
        flash = st.session_state.pop("_flash")
        _disp = {"success": st.success, "warning": st.warning, "error": st.error, "info": st.info}
        _disp.get(flash["level"], st.info)(flash["msg"])

    st.markdown("<br>", unsafe_allow_html=True)

    # ── KPI row ────────────────────────────────────────────────────────────────

    k1, k2, k3, k4, k5 = st.columns(5, gap="small")

    pnl_color      = "kpi-green" if today_pl >= 0 else "kpi-red"
    pnl_pct_str    = f"{today_pl_pct:+.2f}%"
    n_open         = len(positions)
    today_wins     = int((today_closed["exit_reason"] == "eod").sum()) if not today_closed.empty else 0
    today_trades_n = len(today_closed)
    win_rate_str   = f"{today_wins/today_trades_n*100:.0f}%" if today_trades_n else "—"

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

    with k5:
        signals_n = len(state.get("signals", []))
        st.markdown(f"""<div class="kpi-box">
            <div class="kpi-label">Stocks In Play</div>
            <div class="kpi-value kpi-white">{signals_n}</div>
            <div class="kpi-sub">top by RelVol today</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Top 20 Stocks in Play ──────────────────────────────────────────────────

    st.markdown('<p class="section-title">Top 20 Stocks in Play — Today\'s ORB Candidates</p>',
                unsafe_allow_html=True)

    signals = state.get("signals", [])

    if signals:
        rows = []
        for s in signals:
            status  = s.get("status", "not_triggered")
            tag_cls = {"filled": "tag-filled", "pending": "tag-pending"}.get(status, "tag-none")
            tag_lbl = {"filled": "Filled",     "pending": "Pending"    }.get(status, "Not triggered")
            dir_cls = "tag-long" if s["direction"] == "long" else "tag-short"
            dir_lbl = "↑ LONG"  if s["direction"] == "long" else "↓ SHORT"
            risk    = abs(s["entry_price"] - s["stop_loss"])
            rows.append({
                "Symbol":     s["symbol"],
                "_dir_cls":   dir_cls,
                "_dir_lbl":   dir_lbl,
                "RelVol":     f"{s['relvol']:.1f}×",
                "Entry":      f"${s['entry_price']:.2f}",
                "Stop":       f"${s['stop_loss']:.2f}",
                "Risk/Share": f"${risk:.2f}",
                "Shares":     s.get("shares", "—"),
                "_tag_cls":   tag_cls,
                "_tag_lbl":   tag_lbl,
            })

        header_cols = ["Symbol", "Direction", "RelVol", "Entry", "Stop", "Risk/Share", "Shares", "Status"]
        hdr_html = "".join(
            f'<th style="text-align:left;padding:8px 12px;color:#9ca3af;'
            f'font-size:0.70rem;font-weight:600;letter-spacing:0.06em;'
            f'border-bottom:1px solid #1f2937;">{h}</th>'
            for h in header_cols
        )
        rows_html = ""
        for r in rows:
            rows_html += f"""
            <tr style="border-bottom:1px solid #1a1a2e;">
              <td style="padding:8px 12px;color:#f3f4f6;font-weight:600;">{r['Symbol']}</td>
              <td style="padding:8px 12px;"><span class="{r['_dir_cls']}">{r['_dir_lbl']}</span></td>
              <td style="padding:8px 12px;color:#facc15;font-weight:600;">{r['RelVol']}</td>
              <td style="padding:8px 12px;color:#d1d5db;">{r['Entry']}</td>
              <td style="padding:8px 12px;color:#d1d5db;">{r['Stop']}</td>
              <td style="padding:8px 12px;color:#d1d5db;">{r['Risk/Share']}</td>
              <td style="padding:8px 12px;color:#d1d5db;">{r['Shares']}</td>
              <td style="padding:8px 12px;"><span class="{r['_tag_cls']}">{r['_tag_lbl']}</span></td>
            </tr>"""

        st.markdown(f"""
        <div style="background:#13131f;border:1px solid #1f2937;border-radius:10px;overflow:hidden;">
          <table style="width:100%;border-collapse:collapse;">
            <thead><tr style="background:#0f0f1a;">{hdr_html}</tr></thead>
            <tbody>{rows_html}</tbody>
          </table>
        </div>
        """, unsafe_allow_html=True)
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

    # ── Open Positions | Today's Closed Trades ─────────────────────────────────

    left_col, right_col = st.columns(2, gap="large")

    with left_col:
        st.markdown('<p class="section-title">Open Positions (live from Alpaca)</p>',
                    unsafe_allow_html=True)

        state_open = {p["symbol"]: p for p in state.get("open_positions", [])}

        if positions:
            pos_rows = []
            for p in positions:
                sym   = p["symbol"]
                sl    = state_open.get(sym, {}).get("stop_loss", 0.0)
                entry = p["entry_price"]
                curr  = p["current_price"]
                upl   = p["unrealized_pl"]
                risk  = abs(entry - sl) if sl else 0
                pnl_r = (curr - entry) / risk if risk and p["side"] == "long" else \
                        (entry - curr) / risk if risk else 0
                pos_rows.append({
                    "Symbol":     sym,
                    "Side":       p["side"].upper(),
                    "Qty":        p["qty"],
                    "Entry":      f"${entry:.2f}",
                    "Current":    f"${curr:.2f}",
                    "Stop":       f"${sl:.2f}" if sl else "—",
                    "Unreal P&L": f"${upl:+.2f}",
                    "P&L R":      f"{pnl_r:+.2f}R",
                })
            st.dataframe(pd.DataFrame(pos_rows), use_container_width=True, hide_index=True)
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
