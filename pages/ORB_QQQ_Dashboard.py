"""
pages/ORB_QQQ_Dashboard.py — ORB QQQ / TQQQ live dashboard.
"""

from __future__ import annotations

import time
from datetime import datetime, date
from pathlib import Path

import pandas as pd
import streamlit as st
import pytz

ET      = pytz.timezone("America/New_York")
REFRESH = 15

st.set_page_config(
    page_title="ORB QQQ/TQQQ",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
[data-testid="stAppViewContainer"] { background: #0f0f1a; }
[data-testid="stHeader"]           { background: transparent; }
.section-title {
    font-size: 0.70rem; font-weight: 700; letter-spacing: 0.12em;
    text-transform: uppercase; color: #6b7280;
    margin: 24px 0 10px; padding-bottom: 6px;
    border-bottom: 1px solid #1f2937;
}
.kpi-box {
    background: #13131f; border: 1px solid #1f2937;
    border-radius: 10px; padding: 14px 18px; text-align: center;
}
.kpi-label { font-size: 0.70rem; color: #6b7280; text-transform: uppercase; letter-spacing: 0.08em; }
.kpi-value { font-size: 1.7rem; font-weight: 700; margin: 4px 0 0; }
.kpi-sub   { font-size: 0.68rem; color: #6b7280; margin-top: 4px; }
.kpi-green  { color: #4ade80; }
.kpi-red    { color: #f87171; }
.kpi-white  { color: #f3f4f6; }
.kpi-yellow { color: #facc15; }
.card {
    background: #13131f; border: 1px solid #1f2937;
    border-radius: 10px; padding: 20px 22px;
}
.tag-long  { background:#14532d; color:#86efac; border-radius:4px; padding:2px 8px; font-size:0.72rem; font-weight:700; }
.tag-short { background:#7f1d1d; color:#fca5a5; border-radius:4px; padding:2px 8px; font-size:0.72rem; font-weight:700; }
.tag-doji  { background:#1f2937; color:#6b7280; border-radius:4px; padding:2px 8px; font-size:0.72rem; font-weight:700; }
.tag-qqq   { background:#1e3a5f; color:#93c5fd; border-radius:4px; padding:2px 8px; font-size:0.72rem; font-weight:700; }
.tag-tqqq  { background:#14532d; color:#86efac; border-radius:4px; padding:2px 8px; font-size:0.72rem; font-weight:700; }
</style>
""", unsafe_allow_html=True)


# ── Header ─────────────────────────────────────────────────────────────────────

now_et = datetime.now(tz=ET)
st.markdown(f"""
<div style="display:flex;justify-content:space-between;align-items:center;
            margin-bottom:6px;padding-bottom:12px;border-bottom:1px solid #1f2937;">
    <span style="font-size:1.2rem;font-weight:700;color:#f3f4f6;">
        📊 ORB QQQ / TQQQ
        <span style="font-size:0.80rem;font-weight:400;color:#6b7280;margin-left:8px;">
            5-min Opening Range Breakout · Zarattini &amp; Aziz (2023/2025)
        </span>
    </span>
    <span style="font-size:0.75rem;color:#4b5563;">{now_et.strftime('%H:%M ET')}</span>
</div>
""", unsafe_allow_html=True)


# ── Flash messages ─────────────────────────────────────────────────────────────

if "_flash" in st.session_state:
    f = st.session_state.pop("_flash")
    if f["level"] == "success":
        st.success(f["msg"])
    elif f["level"] == "warning":
        st.warning(f["msg"])
    else:
        st.error(f["msg"])


# ── Alpaca account helpers ─────────────────────────────────────────────────────

@st.cache_data(ttl=REFRESH)
def _alpaca_account() -> dict:
    try:
        import config
        if not config.ALPACA_API_KEY:
            return {}
        from alpaca.trading.client import TradingClient
        client = TradingClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
            paper=config.PAPER_TRADING,
        )
        acct = client.get_account()
        equity      = float(acct.equity)
        last_equity = float(acct.last_equity)
        today_pl    = equity - last_equity
        return {
            "equity":       equity,
            "last_equity":  last_equity,
            "buying_power": float(acct.buying_power),
            "today_pl":     today_pl,
            "today_pl_pct": today_pl / last_equity * 100 if last_equity else 0.0,
        }
    except Exception:
        return {}


@st.cache_data(ttl=REFRESH)
def _alpaca_positions() -> list[dict]:
    try:
        import config
        if not config.ALPACA_API_KEY:
            return []
        from alpaca.trading.client import TradingClient
        client = TradingClient(
            api_key=config.ALPACA_API_KEY,
            secret_key=config.ALPACA_SECRET_KEY,
            paper=config.PAPER_TRADING,
        )
        positions = client.get_all_positions()
        return [
            {
                "symbol":   p.symbol,
                "qty":      float(p.qty),
                "side":     p.side.value if hasattr(p.side, "value") else str(p.side),
                "avg_entry": float(p.avg_entry_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc) * 100,
                "current_price": float(p.current_price),
            }
            for p in positions
            if p.symbol in ("QQQ", "TQQQ")
        ]
    except Exception:
        return []


# ── Live Account KPIs ──────────────────────────────────────────────────────────

st.markdown('<p class="section-title">Live Account</p>', unsafe_allow_html=True)

acct = _alpaca_account()

if not acct:
    st.info("No Alpaca credentials configured — showing research/backtest mode only.")
else:
    k1, k2, k3, k4 = st.columns(4, gap="large")

    def _kpi(col, label, value, sub="", color_class="kpi-white"):
        with col:
            st.markdown(f"""
            <div class="kpi-box">
                <div class="kpi-label">{label}</div>
                <div class="kpi-value {color_class}">{value}</div>
                {"" if not sub else f'<div class="kpi-sub">{sub}</div>'}
            </div>
            """, unsafe_allow_html=True)

    pl     = acct["today_pl"]
    pl_pct = acct["today_pl_pct"]
    pl_cls = "kpi-green" if pl >= 0 else "kpi-red"
    pl_sgn = "+" if pl >= 0 else ""

    _kpi(k1, "Equity",       f"${acct['equity']:,.0f}",       color_class="kpi-white")
    _kpi(k2, "Today P&L",    f"{pl_sgn}${pl:,.2f}",           sub=f"{pl_sgn}{pl_pct:.2f}%", color_class=pl_cls)
    _kpi(k3, "Buying Power", f"${acct['buying_power']:,.0f}", color_class="kpi-white")
    _kpi(k4, "Prev. Close Equity", f"${acct['last_equity']:,.0f}", color_class="kpi-white")


# ── Open QQQ/TQQQ Positions ────────────────────────────────────────────────────

positions = _alpaca_positions()

if positions:
    st.markdown('<p class="section-title">Open Positions (QQQ / TQQQ)</p>', unsafe_allow_html=True)
    for p in positions:
        pnl_cls = "kpi-green" if p["unrealized_pl"] >= 0 else "kpi-red"
        pnl_sgn = "+" if p["unrealized_pl"] >= 0 else ""
        tag_cls = "tag-tqqq" if p["symbol"] == "TQQQ" else "tag-qqq"
        st.markdown(f"""
        <div class="card" style="margin-bottom:12px;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <span>
                    <span class="{tag_cls}">{p["symbol"]}</span>
                    &nbsp;
                    <span style="color:#9ca3af;font-size:0.85rem;">
                        {p["qty"]:+.0f} shares &nbsp;·&nbsp;
                        avg entry <b style="color:#f3f4f6;">${p["avg_entry"]:.2f}</b> &nbsp;·&nbsp;
                        current <b style="color:#f3f4f6;">${p["current_price"]:.2f}</b>
                    </span>
                </span>
                <span style="text-align:right;">
                    <span class="{pnl_cls}" style="font-size:1.1rem;font-weight:700;">
                        {pnl_sgn}${p["unrealized_pl"]:,.2f}
                    </span>
                    <span style="color:#6b7280;font-size:0.78rem;margin-left:6px;">
                        ({pnl_sgn}{p["unrealized_plpc"]:.2f}%)
                    </span>
                </span>
            </div>
        </div>
        """, unsafe_allow_html=True)


# ── Live Scan ──────────────────────────────────────────────────────────────────

st.markdown('<p class="section-title">Live Signal Scanner</p>', unsafe_allow_html=True)


def _atr_from_bars(daily: pd.DataFrame, period: int = 14) -> float:
    """Compute ATR from the last `period` daily bars."""
    df = daily.tail(period + 1).copy()
    if len(df) < 2:
        return 0.0
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return float(tr.dropna().tail(period).mean())


def _fetch_atr14(symbol: str, client) -> float:
    """Fetch 14-day ATR — tries parquet cache first, then live Alpaca daily bars."""
    import datetime as dt

    # Try parquet cache first
    try:
        from strategies.orb_qqq.data_fetcher import load_daily
        daily = load_daily(symbol)
        if daily is not None and len(daily) >= 15:
            # Strip timezone for consistent slicing
            if hasattr(daily.index, "tz") and daily.index.tz is not None:
                daily.index = daily.index.tz_localize(None)
            daily = daily[daily.index.normalize() < pd.Timestamp.today().normalize()]
            atr = _atr_from_bars(daily)
            if atr > 0:
                return atr
    except Exception:
        pass

    # Fetch live from Alpaca
    try:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=(date.today() - dt.timedelta(days=30)).isoformat(),
            end=(date.today() - dt.timedelta(days=1)).isoformat(),
            limit=20,
        )
        daily = client.get_stock_bars(req).df
        if isinstance(daily.index, pd.MultiIndex):
            daily = daily.xs(symbol, level="symbol")
        daily = daily.sort_index()
        if hasattr(daily.index, "tz") and daily.index.tz is not None:
            daily.index = daily.index.tz_localize(None)
        atr = _atr_from_bars(daily)
        if atr > 0:
            return atr
    except Exception:
        pass

    return 0.0


def _fetch_scan_signal(symbol: str, variant: str) -> dict:
    """Fetch today's first 5-min candle and compute the ORB signal."""
    try:
        import config
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from strategies.orb_qqq.strategy import generate_signal
        from strategies.orb_qqq.orb_qqq_config import STARTING_CAPITAL

        today = date.today().isoformat()
        client = StockHistoricalDataClient(
            api_key=config.ALPACA_API_KEY or None,
            secret_key=config.ALPACA_SECRET_KEY or None,
        )
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute,
            start=f"{today}T09:30:00",
            end=f"{today}T10:00:00",
            limit=6,
        )
        bars = client.get_stock_bars(req).df
        if bars.empty:
            return {"error": "No intraday bars available yet — scan after 9:35 AM ET."}

        if isinstance(bars.index, pd.MultiIndex):
            bars = bars.xs(symbol, level="symbol")
        bars = bars.sort_index()
        if len(bars) < 2:
            return {"error": "Need at least 2 candles (scan after 9:35 AM ET)."}

        c1      = bars.iloc[0]
        c2_open = float(bars.iloc[1]["open"])
        capital = acct.get("equity", STARTING_CAPITAL) if acct else STARTING_CAPITAL

        atr14 = _fetch_atr14(symbol, client) if variant == "optimised" else 0.0
        if variant == "optimised" and atr14 == 0.0:
            return {"error": "Could not compute ATR14 — daily bars unavailable. Run the data fetcher first."}

        signal = generate_signal(
            symbol=symbol, c1=c1, c2_open=c2_open,
            capital=capital, atr14=atr14, variant=variant,
        )

        c1_dict = {
            "c1_open":  float(c1["open"]),
            "c1_close": float(c1["close"]),
            "c1_high":  float(c1["high"]),
            "c1_low":   float(c1["low"]),
        }

        if signal is None:
            return {
                "symbol": symbol, "variant": variant, "direction": "NO TRADE",
                **c1_dict,
                "entry": None, "stop": None, "target": None, "shares": 0,
                "atr14": round(atr14, 4) if atr14 else None,
                "note": "Doji or stop too tight — no trade today.",
            }

        return {
            "symbol": symbol, "variant": variant,
            "direction": signal.direction.name,
            **c1_dict,
            "entry":    signal.entry,
            "stop":     signal.stop,
            "target":   signal.target,
            "shares":   signal.shares,
            "risk_usd": signal.risk_per_share * signal.shares,
            "atr14":    round(atr14, 4) if atr14 else None,
            "note":     None,
        }
    except Exception as e:
        return {"error": str(e)}


def _render_signal_card(result: dict | None, symbol: str, variant: str):
    label     = f"{symbol} {'Baseline' if variant == 'baseline' else 'Optimised'}"
    tag_sym   = "tag-tqqq" if symbol == "TQQQ" else "tag-qqq"
    var_color = "#facc15" if variant == "optimised" else "#9ca3af"

    if result is None:
        st.markdown(f"""
        <div class="card" style="color:#6b7280;font-size:0.82rem;min-height:80px;">
            <span class="{tag_sym}">{symbol}</span>
            <span style="color:{var_color};font-size:0.70rem;font-weight:700;
                         margin-left:6px;text-transform:uppercase;">{variant}</span>
            <div style="margin-top:10px;">Click "Scan" to fetch today's signal.</div>
        </div>
        """, unsafe_allow_html=True)
        return

    if "error" in result:
        st.markdown(f"""
        <div class="card" style="min-height:80px;">
            <span class="{tag_sym}">{symbol}</span>
            <span style="color:{var_color};font-size:0.70rem;font-weight:700;
                         margin-left:6px;text-transform:uppercase;">{variant}</span>
            <div style="color:#f87171;font-size:0.82rem;margin-top:10px;">{result["error"]}</div>
        </div>
        """, unsafe_allow_html=True)
        return

    direction  = result["direction"]
    dir_cls    = "tag-long" if direction == "LONG" else "tag-short" if direction == "SHORT" else "tag-doji"
    entry_str  = f"${result['entry']:.2f}"  if result["entry"]  else "—"
    stop_str   = f"${result['stop']:.2f}"   if result["stop"]   else "—"
    _tgt       = result["target"]
    target_lbl = "Target (EOD)" if variant == "optimised" else "Target (10R)"
    target_str = "EOD" if _tgt and abs(_tgt) > 1e8 else (f"${_tgt:.2f}" if _tgt else "—")
    risk_str   = f"${result.get('risk_usd', 0):.2f}" if result["entry"] else "—"
    shares_str = str(result["shares"]) if result["shares"] else "—"
    atr_html   = (f'<span style="color:#6b7280;font-size:0.72rem;margin-left:10px;">ATR14: {result["atr14"]}</span>'
                  if result.get("atr14") else "")
    note_html  = (f'<div style="color:#facc15;font-size:0.78rem;margin-top:8px;">⚠ {result["note"]}</div>'
                  if result.get("note") else "")

    st.markdown(f"""
    <div class="card">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:14px;flex-wrap:wrap;">
            <span class="{tag_sym}">{symbol}</span>
            <span style="color:{var_color};font-size:0.70rem;font-weight:700;
                         text-transform:uppercase;">{variant}</span>
            <span class="{dir_cls}">{direction}</span>
            <span style="color:#6b7280;font-size:0.75rem;">
                C1: O {result['c1_open']:.2f} · H {result['c1_high']:.2f} ·
                    L {result['c1_low']:.2f} · C {result['c1_close']:.2f}
            </span>
            {atr_html}
        </div>
        <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px;text-align:center;">
            <div>
                <div style="font-size:0.65rem;color:#6b7280;text-transform:uppercase;letter-spacing:0.06em;">Entry</div>
                <div style="font-size:1.05rem;font-weight:700;color:#f3f4f6;">{entry_str}</div>
            </div>
            <div>
                <div style="font-size:0.65rem;color:#6b7280;text-transform:uppercase;letter-spacing:0.06em;">Stop</div>
                <div style="font-size:1.05rem;font-weight:700;color:#f87171;">{stop_str}</div>
            </div>
            <div>
                <div style="font-size:0.65rem;color:#6b7280;text-transform:uppercase;letter-spacing:0.06em;">{target_lbl}</div>
                <div style="font-size:1.05rem;font-weight:700;color:#4ade80;">{target_str}</div>
            </div>
            <div>
                <div style="font-size:0.65rem;color:#6b7280;text-transform:uppercase;letter-spacing:0.06em;">Shares</div>
                <div style="font-size:1.05rem;font-weight:700;color:#f3f4f6;">{shares_str}</div>
            </div>
            <div>
                <div style="font-size:0.65rem;color:#6b7280;text-transform:uppercase;letter-spacing:0.06em;">Risk $</div>
                <div style="font-size:1.05rem;font-weight:700;color:#facc15;">{risk_str}</div>
            </div>
        </div>
        {note_html}
    </div>
    """, unsafe_allow_html=True)


# 2×2 grid: TQQQ (left) | QQQ (right), Optimised on top, Baseline below
sc_tqqq, sc_qqq = st.columns(2, gap="large")

for col, symbol in [(sc_tqqq, "TQQQ"), (sc_qqq, "QQQ")]:
    with col:
        for variant in ["optimised", "baseline"]:
            scan_key  = f"scan_{symbol}_{variant}"
            btn_label = f"▶ Scan {symbol} {'Optimised' if variant == 'optimised' else 'Baseline'}"
            if st.button(btn_label, key=f"btn_{symbol}_{variant}", use_container_width=True):
                with st.spinner(f"Fetching {symbol} {variant} signal…"):
                    result = _fetch_scan_signal(symbol, variant)
                st.session_state[scan_key] = result
            _render_signal_card(st.session_state.get(scan_key), symbol, variant)
            st.markdown("<br>", unsafe_allow_html=True)


# ── Live Trading Control ───────────────────────────────────────────────────────

st.markdown('<p class="section-title">Live Trading (ORB QQQ / TQQQ)</p>', unsafe_allow_html=True)

import config as _cfg
import os as _os

_PID_FILE   = _cfg.DATA_DIR / "orb_qqq_main.pid"
_STATE_FILE = _cfg.DATA_DIR / "orb_qqq_state.json"


def _trader_running() -> bool:
    if not _PID_FILE.exists():
        return False
    try:
        pid = int(_PID_FILE.read_text().strip().splitlines()[-1])
        _os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, ValueError, OSError):
        _PID_FILE.unlink(missing_ok=True)
        return False


def _read_trader_state() -> dict:
    try:
        if _STATE_FILE.exists():
            import json
            return json.loads(_STATE_FILE.read_text())
    except Exception:
        pass
    return {}


running = _trader_running()
state   = _read_trader_state()

_ctl_left, _ctl_right = st.columns([1, 3], gap="large")

with _ctl_left:
    if running:
        st.markdown("""
        <div class="kpi-box" style="background:#14532d22;border-color:#4ade8055;">
            <div class="kpi-label">Status</div>
            <div class="kpi-value kpi-green" style="font-size:1.1rem;">● RUNNING</div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("⏹ Stop Trading", use_container_width=True, type="secondary"):
            try:
                pid = int(_PID_FILE.read_text().strip().splitlines()[-1])
                import signal as _sig
                _os.kill(pid, _sig.SIGINT)
                st.session_state["_flash"] = {"level": "warning", "msg": "Stop signal sent to trading loop."}
            except Exception as e:
                st.session_state["_flash"] = {"level": "error", "msg": f"Could not stop: {e}"}
            st.rerun()
    else:
        st.markdown("""
        <div class="kpi-box" style="background:#7f1d1d22;border-color:#f8717155;">
            <div class="kpi-label">Status</div>
            <div class="kpi-value kpi-red" style="font-size:1.1rem;">● STOPPED</div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("""
        <div style="font-size:0.70rem;color:#6b7280;margin-bottom:4px;
                    text-transform:uppercase;letter-spacing:0.08em;">Instrument</div>
        <div style="margin-bottom:10px;">
            <span class="tag-tqqq" style="font-size:0.85rem;padding:4px 12px;">TQQQ</span>
        </div>
        """, unsafe_allow_html=True)
        variant_sel = st.selectbox("Variant", ["optimised", "baseline"],
                                   key="live_variant_sel", label_visibility="collapsed")
        if st.button("▶ Start Trading", use_container_width=True, type="primary"):
            import subprocess, sys
            try:
                proc = subprocess.Popen(
                    [sys.executable, "orb_qqq_main.py",
                     "--variant", variant_sel, "--symbols", "TQQQ"],
                    cwd=str(_cfg.BASE_DIR),
                    stdout=open(_cfg.LOGS_DIR / "orb_qqq_main.log", "a"),
                    stderr=subprocess.STDOUT,
                )
                st.session_state["_flash"] = {
                    "level": "success",
                    "msg": f"ORB TQQQ live loop started (PID {proc.pid}).",
                }
            except Exception as e:
                st.session_state["_flash"] = {"level": "error", "msg": f"Could not start: {e}"}
            st.rerun()

with _ctl_right:
    if state:
        updated     = state.get("updated_at", "")
        phase       = state.get("phase", "")
        v_label     = state.get("variant", "")
        atr_pct     = state.get("atr_stop_pct", 0.0)
        instruments = state.get("instruments", [])
        inst_html   = " ".join(
            f'<span class="{"tag-tqqq" if i == "TQQQ" else "tag-qqq"}">{i}</span>'
            for i in instruments
        )

        st.markdown(
            f'<div style="font-size:0.72rem;color:#6b7280;margin-bottom:10px;">'
            f'Trading: {inst_html} &nbsp;·&nbsp; '
            f'Variant: <b style="color:#facc15;">{v_label}</b> &nbsp;·&nbsp; '
            f'ATR stop: <b style="color:#facc15;">{atr_pct:.0%}</b> &nbsp;·&nbsp; '
            f'Phase: <b style="color:#9ca3af;">{phase}</b> &nbsp;·&nbsp; '
            f'Updated: <b style="color:#9ca3af;">{updated[11:19]} ET</b>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Active positions
        positions_live = state.get("positions", [])
        if positions_live:
            st.markdown('<div style="font-size:0.75rem;font-weight:700;color:#9ca3af;'
                        'margin-bottom:6px;text-transform:uppercase;">Open Positions</div>',
                        unsafe_allow_html=True)
            for p in positions_live:
                dir_cls = "tag-long" if p["direction"] == "long" else "tag-short"
                sl_badge = ('<span style="color:#4ade80;font-size:0.70rem;">SL ✓</span>'
                            if p.get("has_sl") else
                            '<span style="color:#facc15;font-size:0.70rem;">SL pending</span>')
                tag_cls = "tag-tqqq" if p["symbol"] == "TQQQ" else "tag-qqq"
                st.markdown(f"""
                <div class="card" style="margin-bottom:8px;padding:12px 18px;">
                    <span class="{tag_cls}">{p["symbol"]}</span>
                    <span class="{dir_cls}" style="margin-left:6px;">{p["direction"].upper()}</span>
                    &nbsp;
                    <span style="color:#9ca3af;font-size:0.82rem;">
                        {p["qty"]} shares &nbsp;·&nbsp;
                        entry <b style="color:#f3f4f6;">${p["entry_price"]:.2f}</b> &nbsp;·&nbsp;
                        stop <b style="color:#f87171;">${p["stop"]:.2f}</b>
                    </span>
                    &nbsp; {sl_badge}
                </div>
                """, unsafe_allow_html=True)

        # Signals (if no positions yet)
        elif state.get("signals"):
            st.markdown('<div style="font-size:0.75rem;font-weight:700;color:#9ca3af;'
                        'margin-bottom:6px;text-transform:uppercase;">Today\'s Signals</div>',
                        unsafe_allow_html=True)
            for s in state["signals"]:
                dir_cls = "tag-long" if s["direction"] == "long" else "tag-short"
                tag_cls = "tag-tqqq" if s["symbol"] == "TQQQ" else "tag-qqq"
                st.markdown(f"""
                <div class="card" style="margin-bottom:8px;padding:12px 18px;">
                    <span class="{tag_cls}">{s["symbol"]}</span>
                    <span class="{dir_cls}" style="margin-left:6px;">{s["direction"].upper()}</span>
                    &nbsp;
                    <span style="color:#9ca3af;font-size:0.82rem;">
                        entry <b style="color:#f3f4f6;">${s["entry"]:.2f}</b> &nbsp;·&nbsp;
                        stop <b style="color:#f87171;">${s["stop"]:.2f}</b> &nbsp;·&nbsp;
                        {s["shares"]} shares &nbsp;·&nbsp; risk
                        <b style="color:#facc15;">${s["risk_usd"]:.2f}</b>
                    </span>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.markdown('<div style="color:#6b7280;font-size:0.82rem;">No signals yet today.</div>',
                        unsafe_allow_html=True)
    else:
        st.markdown('<div style="color:#6b7280;font-size:0.82rem;">Trader not running — no state available.</div>',
                    unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)


# ── Backtest helpers ───────────────────────────────────────────────────────────

def _load_results(instrument: str, variant: str, tag: str = ""):
    try:
        from strategies.orb_qqq.orb_qqq_config import backtest_report_path, backtest_trades_path
        rp = backtest_report_path(instrument, variant, tag=tag)
        tp = backtest_trades_path(instrument, variant, tag=tag)
        if rp.exists():
            return pd.read_csv(rp), pd.read_csv(tp) if tp.exists() else None
    except Exception:
        pass
    return None, None


def _bh_equity(symbol: str, start_date, end_date, capital: float = 25_000.0):
    """Return a daily B&H equity Series for `symbol` over the given period."""
    try:
        from strategies.orb_qqq.data_fetcher import load_daily
        daily = load_daily(symbol)
        if daily is None or daily.empty:
            return None
        # Normalise timezone
        if hasattr(daily.index, "tz") and daily.index.tz is not None:
            daily.index = daily.index.tz_localize(None)
        daily = daily.sort_index()
        sd = pd.Timestamp(start_date).normalize()
        ed = pd.Timestamp(end_date).normalize()
        daily = daily[(daily.index.normalize() >= sd) & (daily.index.normalize() <= ed)]
        if daily.empty:
            return None
        prices = daily["close"]
        return capital * prices / prices.iloc[0]
    except Exception:
        return None


def _build_atr_chart(
    symbol:      str,
    sweep_equity: dict,       # label → equity Series
    sweep_rows:  list,
    best_s_idx:  int,
    title_prefix: str,
) -> "go.Figure":
    """Build a Plotly figure with ATR equity curves + QQQ and TQQQ B&H overlays."""
    import plotly.graph_objects as go

    palette = ["#93c5fd","#86efac","#fcd34d","#f9a8d4",
               "#c4b5fd","#6ee7b7","#fb923c"]

    fig = go.Figure()

    # ATR strategy lines
    for i, (lbl, series) in enumerate(sweep_equity.items()):
        is_best = (lbl == sweep_rows[best_s_idx]["ATR %"])
        fig.add_trace(go.Scatter(
            x    = series.index,
            y    = series.values,
            name = f"ATR {lbl}" + (" ★" if is_best else ""),
            line = dict(
                color = palette[i % len(palette)],
                width = 2.5 if is_best else 1.2,
                dash  = "solid" if is_best else "dot",
            ),
            hovertemplate=f"ATR {lbl}<br>%{{x|%Y-%m-%d}}<br>${{y:,.0f}}<extra></extra>",
        ))

    # B&H overlays — infer period from sweep data
    if sweep_equity:
        all_dates = [s.index for s in sweep_equity.values() if len(s)]
        start_dt  = min(s.min() for s in all_dates)
        end_dt    = max(s.max() for s in all_dates)
        capital   = list(sweep_equity.values())[0].iloc[0]

        for bh_sym, bh_color, bh_dash in [
            ("QQQ",  "#f87171", "solid"),
            ("TQQQ", "#4ade80", "solid"),
        ]:
            bh = _bh_equity(bh_sym, start_dt, end_dt, capital)
            if bh is not None:
                fig.add_trace(go.Scatter(
                    x    = bh.index,
                    y    = bh.values,
                    name = f"{bh_sym} B&H",
                    line = dict(color=bh_color, width=1.5, dash=bh_dash),
                    hovertemplate=(
                        f"{bh_sym} B&H<br>%{{x|%Y-%m-%d}}<br>${{y:,.0f}}<extra></extra>"
                    ),
                ))

    fig.update_layout(
        paper_bgcolor = "#0f0f1a",
        plot_bgcolor  = "#13131f",
        font          = dict(color="#9ca3af", size=11),
        height        = 360,
        margin        = dict(l=10, r=10, t=36, b=10),
        legend        = dict(bgcolor="#13131f", bordercolor="#1f2937",
                             borderwidth=1, font=dict(size=11)),
        xaxis = dict(gridcolor="#1f2937"),
        yaxis = dict(gridcolor="#1f2937", tickprefix="$", tickformat=",.0f"),
        title = dict(
            text     = f"{title_prefix} — Equity curves by ATR stop (★ best Sharpe · grey = B&H)",
            font     = dict(size=12, color="#6b7280"),
            x=0.01, xanchor="left",
        ),
    )
    return fig


def _render_backtest_section(title: str, tag: str, run_cmd: str, period_tag: str = ""):
    st.markdown(f'<p class="section-title">{title}</p>', unsafe_allow_html=True)

    combos = [
        ("QQQ",  "baseline",  "QQQ Baseline",  "#93c5fd"),
        ("QQQ",  "optimised", "QQQ Optimised", "#93c5fd"),
        ("TQQQ", "baseline",  "TQQQ Baseline", "#86efac"),
        ("TQQQ", "optimised", "TQQQ Optimised","#86efac"),
    ]
    sum_cols = st.columns(4, gap="large")
    for col, (inst, var, label, color) in zip(sum_cols, combos):
        r, _ = _load_results(inst, var, tag=tag)
        with col:
            if r is not None:
                row = r.iloc[0]
                st.markdown(f"""
                <div class="card" style="text-align:center;padding:18px 12px;">
                    <div style="font-size:0.72rem;font-weight:700;color:{color};
                                text-transform:uppercase;letter-spacing:0.08em;margin-bottom:10px;">{label}</div>
                    <div style="font-size:1.8rem;font-weight:800;color:{color};">
                        {row.get('total_return_pct', 0):.0f}%</div>
                    <div style="font-size:0.68rem;color:#6b7280;text-transform:uppercase;
                                letter-spacing:0.08em;margin-bottom:10px;">Total Return</div>
                    <div style="font-size:0.80rem;color:#9ca3af;line-height:2.0;">
                        Ann: <b style="color:#f3f4f6;">{row.get('ann_return_pct', 0):.1f}%</b> &nbsp;
                        Sharpe: <b style="color:#f3f4f6;">{row.get('sharpe_ratio', 0):.2f}</b> &nbsp;
                        MDD: <b style="color:#f87171;">{row.get('max_drawdown_pct', 0):.1f}%</b><br>
                        Final equity: <b style="color:#f3f4f6;">${row.get('final_equity', 0):,.0f}</b>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div class="card" style="text-align:center;padding:18px 12px;color:#6b7280;font-size:0.82rem;">
                    <b style="color:{color};">{label}</b><br><br>No results yet.<br>
                    <code style="font-size:0.70rem;">{run_cmd}</code>
                </div>
                """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    tabs = st.tabs(["QQQ — Baseline", "QQQ — Optimised", "TQQQ — Baseline", "TQQQ — Optimised"])
    for tab, (instrument, variant) in zip(tabs, [
        ("QQQ", "baseline"), ("QQQ", "optimised"),
        ("TQQQ", "baseline"), ("TQQQ", "optimised"),
    ]):
        with tab:
            report, trades = _load_results(instrument, variant, tag=tag)
            if report is None:
                st.markdown(f"""
                <div style="color:#6b7280;font-size:0.82rem;padding:16px 0;">
                No results yet — run: <code>{run_cmd}</code>
                </div>
                """, unsafe_allow_html=True)
                continue

            row = report.iloc[0]
            m1, m2, m3, m4, m5, m6 = st.columns(6, gap="large")
            for col, (val, label, is_neg) in zip([m1, m2, m3, m4, m5, m6], [
                (f"{row.get('total_return_pct', 0):.1f}%", "Total Return",  False),
                (f"{row.get('ann_return_pct',   0):.1f}%", "Ann. Return",   False),
                (f"{row.get('sharpe_ratio',     0):.2f}",  "Sharpe",        False),
                (f"{row.get('max_drawdown_pct', 0):.1f}%", "Max Drawdown",  True),
                (f"{row.get('win_rate_pct',     0):.1f}%", "Win Rate",      False),
                (f"{row.get('total_trades',     0):.0f}",  "Total Trades",  False),
            ]):
                with col:
                    color = "#f87171" if is_neg else ("#f87171" if val.startswith("-") else "#4ade80")
                    st.markdown(f"""
                    <div class="card" style="text-align:center;padding:14px 8px;">
                        <div style="font-size:1.3rem;font-weight:700;color:{color};">{val}</div>
                        <div style="font-size:0.68rem;color:#6b7280;text-transform:uppercase;
                                    letter-spacing:0.08em;margin-top:4px;">{label}</div>
                    </div>
                    """, unsafe_allow_html=True)

            # ── ATR sweep chart (optimised only, when sweep has been run) ──────
            if variant == "optimised" and period_tag:
                import plotly.graph_objects as go
                palette      = ["#93c5fd","#86efac","#fcd34d","#f9a8d4",
                                "#c4b5fd","#6ee7b7","#fb923c"]
                sweep_rows   = []
                sweep_equity = {}

                for pct in [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35]:
                    sweep_tag = f"{period_tag}_atr{int(pct * 100):02d}"
                    sr, st2   = _load_results(instrument, "optimised", tag=sweep_tag)
                    if sr is not None:
                        m = sr.iloc[0]
                        sweep_rows.append({
                            "ATR %":        f"{pct:.0%}",
                            "Total Ret":    f"{m.get('total_return_pct', 0):.1f}%",
                            "Ann Ret":      f"{m.get('ann_return_pct',   0):.1f}%",
                            "Sharpe":       f"{m.get('sharpe_ratio',     0):.3f}",
                            "MDD":          f"{m.get('max_drawdown_pct', 0):.1f}%",
                            "Win %":        f"{m.get('win_rate_pct',     0):.1f}%",
                            "Stops":        int(m.get('stop_exits',      0)),
                            "EOD exits":    int(m.get('eod_exits',       0)),
                            "Final Equity": f"${m.get('final_equity',    0):,.0f}",
                            "_sharpe_raw":  m.get('sharpe_ratio',     0),
                            "_mdd_raw":     m.get('max_drawdown_pct', 0),
                        })
                        if st2 is not None:
                            try:
                                t2 = st2.copy()
                                t2["date"] = pd.to_datetime(t2["date"])
                                t2.sort_values("date", inplace=True)
                                daily = t2.groupby("date")["net_pnl"].sum()
                                sweep_equity[f"{pct:.0%}"] = \
                                    float(m.get("initial_equity", 25000)) + daily.cumsum()
                            except Exception:
                                pass

                if sweep_rows:
                    best_s = max(range(len(sweep_rows)),
                                 key=lambda i: sweep_rows[i]["_sharpe_raw"])
                    best_m = min(range(len(sweep_rows)),
                                 key=lambda i: sweep_rows[i]["_mdd_raw"])

                    if sweep_equity:
                        import plotly.graph_objects as go
                        fig = _build_atr_chart(
                            symbol       = instrument,
                            sweep_equity = sweep_equity,
                            sweep_rows   = sweep_rows,
                            best_s_idx   = best_s,
                            title_prefix = f"{instrument} Optimised",
                        )
                        st.plotly_chart(fig, use_container_width=True)

                    # Table
                    st.markdown(
                        f'<div style="font-size:0.75rem;color:#6b7280;margin-bottom:8px;">'
                        f'🏆 Best Sharpe: <b style="color:#4ade80;">'
                        f'{sweep_rows[best_s]["ATR %"]}</b> &nbsp;·&nbsp; '
                        f'🛡 Lowest MDD: <b style="color:#4ade80;">'
                        f'{sweep_rows[best_m]["ATR %"]}</b></div>',
                        unsafe_allow_html=True,
                    )

                    def _hl(row, bi=best_s):
                        if row.name == bi:
                            return ["background-color:#14532d22;font-weight:bold"] * len(row)
                        return [""] * len(row)

                    disp = ["ATR %","Total Ret","Ann Ret","Sharpe","MDD",
                            "Win %","Stops","EOD exits","Final Equity"]
                    st.dataframe(
                        pd.DataFrame(sweep_rows)[disp].style.apply(_hl, axis=1),
                        use_container_width=True, hide_index=True,
                    )
                    st.markdown("<br>", unsafe_allow_html=True)
                else:
                    st.markdown(
                        f'<div style="color:#6b7280;font-size:0.78rem;margin-bottom:8px;">'
                        f'ATR sweep not run yet — '
                        f'<code>python run_oos_sweep.py --period {period_tag} --skip-download</code>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

            # ── Single equity curve (all variants) ───────────────────────────
            if trades is not None:
                st.markdown("<br>", unsafe_allow_html=True)
                try:
                    t = trades.copy()
                    t["date"] = pd.to_datetime(t["date"])
                    t.sort_values("date", inplace=True)
                    daily_pnl = t.groupby("date")["net_pnl"].sum()
                    eq_start  = float(row.get("initial_equity", 25000))
                    equity    = (eq_start + daily_pnl.cumsum()).reset_index()
                    equity.columns = ["date", "equity"]
                    color_line = "#86efac" if instrument == "TQQQ" else "#93c5fd"
                    st.line_chart(equity.set_index("date"), height=280, color=color_line)
                except Exception as e:
                    st.warning(f"Could not render equity curve: {e}")

            col_left, col_right = st.columns(2)
            with col_left:
                with st.expander("All metrics", expanded=False):
                    st.dataframe(
                        report.T.reset_index().rename(columns={"index": "Metric", 0: "Value"}),
                        use_container_width=True, hide_index=True,
                    )
            with col_right:
                if trades is not None:
                    with st.expander("Trade log", expanded=False):
                        st.dataframe(trades, use_container_width=True, hide_index=True)


# ── Backtest Results: Paper period ─────────────────────────────────────────────

_render_backtest_section(
    title      = "Backtest Results  ·  Jan 2016 – Feb 2023  ·  $25k starting capital",
    tag        = "",
    run_cmd    = "python run_orb_tqqq_backtest.py --skip-download",
    period_tag = "paper",
)

# ── Out-of-Sample Results: 2024–2026 ──────────────────────────────────────────

st.markdown(
    '<p class="section-title">Out-of-Sample Results  ·  Jan 2024 – May 2026  ·  $25k starting capital</p>',
    unsafe_allow_html=True,
)

_OOS_ATR_PCTS = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35]
_OOS_METRICS  = [
    ("total_return_pct", "Total Ret %", "{:.1f}%",  False),
    ("ann_return_pct",   "Ann Ret %",   "{:.1f}%",  False),
    ("sharpe_ratio",     "Sharpe",      "{:.3f}",   False),
    ("max_drawdown_pct", "MDD %",       "{:.1f}%",  True),
    ("win_rate_pct",     "Win %",       "{:.1f}%",  False),
    ("stop_exits",       "Stops",       "{:.0f}",   True),
    ("eod_exits",        "EOD exits",   "{:.0f}",   False),
]

_oos_tab_qqq_base, _oos_tab_qqq_opt, _oos_tab_tqqq_base, _oos_tab_tqqq_opt = st.tabs([
    "QQQ — Baseline", "QQQ — Optimised", "TQQQ — Baseline", "TQQQ — Optimised"
])

# Baseline tabs — single result card
for tab, symbol in [(_oos_tab_qqq_base, "QQQ"), (_oos_tab_tqqq_base, "TQQQ")]:
    with tab:
        report, trades = _load_results(symbol, "baseline", tag="oos")
        if report is None:
            st.markdown("""
            <div style="color:#6b7280;font-size:0.82rem;padding:16px 0;">
            No results yet — run: <code>python run_oos_sweep.py --skip-download</code>
            </div>
            """, unsafe_allow_html=True)
        else:
            row = report.iloc[0]
            color = "#86efac" if symbol == "TQQQ" else "#93c5fd"
            cols = st.columns(6, gap="large")
            for col, (key, label, fmt, is_neg) in zip(cols, _OOS_METRICS[:6]):
                val = fmt.format(row.get(key, 0))
                c   = "#f87171" if is_neg else ("#f87171" if val.startswith("-") else "#4ade80")
                with col:
                    st.markdown(f"""
                    <div class="card" style="text-align:center;padding:14px 8px;">
                        <div style="font-size:1.3rem;font-weight:700;color:{c};">{val}</div>
                        <div style="font-size:0.68rem;color:#6b7280;text-transform:uppercase;
                                    letter-spacing:0.08em;margin-top:4px;">{label}</div>
                    </div>
                    """, unsafe_allow_html=True)
            if trades is not None:
                st.markdown("<br>", unsafe_allow_html=True)
                try:
                    t = trades.copy()
                    t["date"] = pd.to_datetime(t["date"])
                    t.sort_values("date", inplace=True)
                    daily_pnl = t.groupby("date")["net_pnl"].sum()
                    eq = (float(row.get("initial_equity", 25000)) + daily_pnl.cumsum()).reset_index()
                    eq.columns = ["date", "equity"]
                    st.line_chart(eq.set_index("date"), height=240, color=color)
                except Exception as e:
                    st.warning(f"Could not render equity curve: {e}")

# Optimised tabs — ATR sweep table + equity curves
for tab, symbol in [(_oos_tab_qqq_opt, "QQQ"), (_oos_tab_tqqq_opt, "TQQQ")]:
    with tab:
        rows        = []
        equity_data = {}   # pct → equity Series keyed by date

        for pct in _OOS_ATR_PCTS:
            tag = f"oos_atr{int(pct * 100):02d}"
            r, t = _load_results(symbol, "optimised", tag=tag)
            if r is not None:
                m = r.iloc[0]
                rows.append({
                    "ATR %":        f"{pct:.0%}",
                    "Total Ret":    f"{m.get('total_return_pct', 0):.1f}%",
                    "Ann Ret":      f"{m.get('ann_return_pct',   0):.1f}%",
                    "Sharpe":       f"{m.get('sharpe_ratio',     0):.3f}",
                    "MDD":          f"{m.get('max_drawdown_pct', 0):.1f}%",
                    "Win %":        f"{m.get('win_rate_pct',     0):.1f}%",
                    "Stops":        int(m.get('stop_exits',      0)),
                    "EOD exits":    int(m.get('eod_exits',       0)),
                    "Final Equity": f"${m.get('final_equity',    0):,.0f}",
                    "_sharpe_raw":  m.get('sharpe_ratio',     0),
                    "_mdd_raw":     m.get('max_drawdown_pct', 0),
                })
                # Build equity curve for this ATR value
                if t is not None:
                    try:
                        t2 = t.copy()
                        t2["date"] = pd.to_datetime(t2["date"])
                        t2.sort_values("date", inplace=True)
                        daily_pnl = t2.groupby("date")["net_pnl"].sum()
                        eq_start  = float(m.get("initial_equity", 25000))
                        equity_data[f"{pct:.0%}"] = eq_start + daily_pnl.cumsum()
                    except Exception:
                        pass

        if not rows:
            st.markdown("""
            <div style="color:#6b7280;font-size:0.82rem;padding:16px 0;">
            No results yet — run: <code>python run_oos_sweep.py --skip-download</code>
            </div>
            """, unsafe_allow_html=True)
            continue

        best_sharpe_idx = max(range(len(rows)), key=lambda i: rows[i]["_sharpe_raw"])
        best_mdd_idx    = min(range(len(rows)), key=lambda i: rows[i]["_mdd_raw"])

        # ── Equity curve chart (all ATR values + B&H overlays) ───────────────
        if equity_data:
            import plotly.graph_objects as go
            fig = _build_atr_chart(
                symbol        = symbol,
                sweep_equity  = equity_data,
                sweep_rows    = rows,
                best_s_idx    = best_sharpe_idx,
                title_prefix  = f"{symbol} Optimised",
            )
            st.plotly_chart(fig, use_container_width=True)

        # ── Summary table ─────────────────────────────────────────────────────
        st.markdown(f"""
        <div style="font-size:0.75rem;color:#6b7280;margin-bottom:8px;">
            🏆 Best Sharpe: <b style="color:#4ade80;">{rows[best_sharpe_idx]['ATR %']}</b>
            &nbsp;·&nbsp;
            🛡 Lowest MDD: <b style="color:#4ade80;">{rows[best_mdd_idx]['ATR %']}</b>
        </div>
        """, unsafe_allow_html=True)

        display_cols = ["ATR %", "Total Ret", "Ann Ret", "Sharpe", "MDD",
                        "Win %", "Stops", "EOD exits", "Final Equity"]

        def _highlight(row, best_idx=best_sharpe_idx):
            if row.name == best_idx:
                return ["background-color: #14532d22; font-weight: bold"] * len(row)
            return [""] * len(row)

        st.dataframe(
            pd.DataFrame(rows)[display_cols].style.apply(_highlight, axis=1),
            use_container_width=True,
            hide_index=True,
        )


# ── Strategy Parameters ────────────────────────────────────────────────────────

with st.expander("Strategy Parameters", expanded=False):
    try:
        from strategies.orb_qqq import orb_qqq_config as cfg
        params = {
            "Instruments":              ", ".join(cfg.INSTRUMENTS),
            "Risk per trade":           f"{cfg.RISK_PER_TRADE:.0%}",
            "Max leverage":             f"{cfg.MAX_LEVERAGE:.0f}×",
            "Profit target (baseline)": f"{cfg.TARGET_R:.0f}R or EOD",
            "Stop (baseline)":          "1st-candle low / high",
            "ATR period (optimised)":   cfg.ATR_PERIOD,
            "ATR stop % (optimised)":   f"{cfg.ATR_STOP_PCT:.0%}",
            "Target (optimised)":       "EOD only",
            "Starting capital":         f"${cfg.STARTING_CAPITAL:,.0f}",
            "Commission":               f"${cfg.COMMISSION}/share",
            "Session":                  "9:30–16:00 ET (day trade only)",
            "Data source":              "Alpaca (5-min + daily)",
        }
        st.dataframe(
            pd.DataFrame(params.items(), columns=["Parameter", "Value"]),
            use_container_width=True, hide_index=True,
        )
    except Exception as e:
        st.error(f"Could not load config: {e}")


# ── Auto-refresh ───────────────────────────────────────────────────────────────

time.sleep(REFRESH)
st.rerun()
