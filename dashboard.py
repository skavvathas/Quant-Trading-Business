"""
dashboard.py — Streamlit real-time monitor + daily trading plan configurator.

Run:
    streamlit run dashboard.py

Top section: choose today's strategy and stock universe.
Bottom section: live KPI cards, open positions, charts, signal log.
Auto-refreshes every 10 seconds (monitoring only; plan selections persist).
"""

import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import pytz

import config
from session_manager import load_session_config, save_session_config, get_updated_at
from data_manager import get_vix, fetch_daily_bars, fetch_5min_bars
from signals import VIXGate

ET = pytz.timezone("America/New_York")
REFRESH_SECONDS = 10

# ── Page config ────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Trading Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Global CSS ─────────────────────────────────────────────────────────────────

st.markdown("""
<style>
/* ── General ── */
[data-testid="stAppViewContainer"] { background: #0f0f1a; }
[data-testid="stHeader"] { background: transparent; }

/* ── Section headers ── */
.section-title {
    font-size: 0.70rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #6b7280;
    margin-bottom: 10px;
    padding-bottom: 6px;
    border-bottom: 1px solid #1f2937;
}

/* ── Strategy cards ── */
.strat-card {
    border: 1.5px solid #1f2937;
    border-radius: 10px;
    padding: 14px 16px;
    background: #13131f;
    position: relative;
    transition: border-color 0.15s;
    min-height: 145px;
}
.strat-card.selected {
    border-color: #22c55e;
    background: #0d1f12;
}
.strat-card.soon {
    opacity: 0.45;
}
.strat-name {
    font-size: 0.92rem;
    font-weight: 700;
    color: #f3f4f6;
    display: block;
    margin-bottom: 4px;
}
.strat-desc {
    font-size: 0.76rem;
    color: #9ca3af;
    line-height: 1.45;
    margin-bottom: 8px;
}
.strat-params {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: 6px;
}
.param-chip {
    font-size: 0.66rem;
    background: #1f2937;
    color: #d1d5db;
    border-radius: 4px;
    padding: 2px 7px;
}
.badge {
    position: absolute;
    top: 10px;
    right: 12px;
    font-size: 0.60rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    border-radius: 4px;
    padding: 2px 7px;
}
.badge-live { background: #14532d; color: #86efac; }
.badge-beta { background: #713f12; color: #fde68a; }
.badge-soon { background: #1f2937; color: #6b7280; }
.check-icon {
    color: #22c55e;
    font-size: 1.0rem;
    margin-right: 5px;
}

/* ── Stock universe tiles ── */
.stock-tile-selected {
    border: 1.5px solid #3b82f6 !important;
    background: #0c1929 !important;
    border-radius: 8px;
}
.stock-ticker {
    font-size: 1.0rem;
    font-weight: 700;
    color: #f3f4f6;
}
.stock-name {
    font-size: 0.68rem;
    color: #6b7280;
}

/* ── Active plan banner ── */
.active-banner {
    background: #0d1f12;
    border: 1px solid #166534;
    border-radius: 8px;
    padding: 10px 16px;
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 0.82rem;
    color: #d1fae5;
    margin-bottom: 4px;
}
.banner-label { color: #6b7280; font-size: 0.72rem; }

/* ── KPI metric ── */
.kpi-box {
    background: #13131f;
    border: 1px solid #1f2937;
    border-radius: 10px;
    padding: 14px 18px;
    text-align: center;
}
.kpi-label { font-size: 0.70rem; color: #6b7280; text-transform: uppercase;
             letter-spacing: 0.08em; }
.kpi-value { font-size: 1.7rem; font-weight: 700; margin: 4px 0 0; }
.kpi-green { color: #4ade80; }
.kpi-red   { color: #f87171; }
.kpi-yellow{ color: #facc15; }
.kpi-white { color: #f3f4f6; }
</style>
""", unsafe_allow_html=True)


# ── Session state bootstrap ────────────────────────────────────────────────────
# Runs only ONCE per browser session — subsequent reruns (incl. auto-refresh)
# preserve selections via st.session_state.

if "plan_initialized" not in st.session_state:
    saved = load_session_config()
    st.session_state.selected_strategy = saved["strategy"]
    st.session_state.selected_symbols = set(saved["symbols"])
    st.session_state.plan_initialized = True
    st.session_state.save_success = False


# ── Cached data helpers ────────────────────────────────────────────────────────

@st.cache_data(ttl=REFRESH_SECONDS)
def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=60)
def _realized_vol_series(symbol: str, lookback: int = 60) -> pd.DataFrame:
    daily = fetch_daily_bars(symbol, limit=lookback + 25)
    if daily.empty:
        return pd.DataFrame()
    rv = daily["close"].pct_change().rolling(20).std() * np.sqrt(252)
    df = pd.DataFrame({"date": rv.index, "rv": rv.values, "symbol": symbol})
    return df.dropna().tail(lookback)


@st.cache_data(ttl=REFRESH_SECONDS)
def _zscore_series(symbol: str, n: int = 100) -> pd.DataFrame:
    bars = fetch_5min_bars(symbol, limit=n)
    if bars.empty:
        return pd.DataFrame()
    c = bars["close"]
    sma = c.rolling(20).mean()
    std = c.rolling(20).std(ddof=1).replace(0, np.nan)
    z = (c - sma) / std
    return pd.DataFrame({"ts": bars.index, "z": z.values, "close": c.values})


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1: TODAY'S TRADING PLAN
# ═══════════════════════════════════════════════════════════════════════════════

st.markdown('<p class="section-title">Today\'s Trading Plan</p>', unsafe_allow_html=True)

plan_left, plan_right = st.columns([5, 4], gap="large")

# ── Strategy selector (left column) ───────────────────────────────────────────
with plan_left:
    st.markdown('<p class="section-title" style="margin-top:0">Strategies</p>',
                unsafe_allow_html=True)

    n_strats = len(config.AVAILABLE_STRATEGIES)
    strat_cols = st.columns(n_strats, gap="small")

    for col, (key, strat) in zip(strat_cols, config.AVAILABLE_STRATEGIES.items()):
        with col:
            is_selected = st.session_state.selected_strategy == key
            is_live = strat["status"] == "live"
            card_cls = "selected" if is_selected else ("soon" if not is_live else "strat-card")
            if is_selected:
                card_cls = "strat-card selected"
            elif not is_live:
                card_cls = "strat-card soon"
            else:
                card_cls = "strat-card"

            params_html = "".join(
                f'<span class="param-chip">{k}: {v}</span>'
                for k, v in strat["params"].items()
            )
            badge_cls = f"badge badge-{strat['status']}"
            badge_label = {
                "live": "LIVE", "beta": "BETA", "soon": "SOON"
            }.get(strat["status"], strat["status"].upper())
            check = '<span class="check-icon">✓</span>' if is_selected else ""

            st.markdown(f"""
            <div class="{card_cls}">
                <span class="{badge_cls}">{badge_label}</span>
                <span class="strat-name">{check}{strat['name']}</span>
                <div class="strat-desc">{strat['description']}</div>
                <div class="strat-params">{params_html}</div>
            </div>
            """, unsafe_allow_html=True)

            # Select button (only for live/beta, only when not already selected)
            if is_live and not is_selected:
                if st.button("Select", key=f"sel_{key}", use_container_width=True):
                    st.session_state.selected_strategy = key
                    st.session_state.save_success = False
                    st.rerun()
            elif is_selected:
                st.markdown(
                    '<div style="text-align:center;font-size:0.75rem;color:#22c55e;'
                    'margin-top:6px;">● Active</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<div style="text-align:center;font-size:0.72rem;color:#4b5563;'
                    'margin-top:6px;">Coming soon</div>',
                    unsafe_allow_html=True,
                )

# ── Stock universe selector (right column) ─────────────────────────────────────
with plan_right:
    n_sel = len(st.session_state.selected_symbols)
    st.markdown(
        f'<p class="section-title" style="margin-top:0">Stock Universe '
        f'<span style="color:#3b82f6;font-weight:700;">{n_sel} selected</span></p>',
        unsafe_allow_html=True,
    )

    tickers = list(config.FULL_UNIVERSE.keys())
    # 5 columns × 2 rows
    COLS_PER_ROW = 5
    for row_start in range(0, len(tickers), COLS_PER_ROW):
        row_tickers = tickers[row_start : row_start + COLS_PER_ROW]
        row_cols = st.columns(COLS_PER_ROW, gap="small")
        for col, ticker in zip(row_cols, row_tickers):
            with col:
                is_on = ticker in st.session_state.selected_symbols
                company = config.FULL_UNIVERSE[ticker]
                short_name = company if len(company) <= 9 else company[:8] + "…"

                # Primary (blue) = selected, Secondary (outline) = not selected
                btn_label = f"{'✓ ' if is_on else ''}{ticker}"
                if st.button(
                    btn_label,
                    key=f"stock_btn_{ticker}",
                    type="primary" if is_on else "secondary",
                    use_container_width=True,
                    help=company,
                ):
                    if is_on:
                        st.session_state.selected_symbols.discard(ticker)
                    else:
                        st.session_state.selected_symbols.add(ticker)
                    st.session_state.save_success = False
                    st.rerun()

                st.markdown(
                    f'<div class="stock-name" style="text-align:center;margin-top:2px;">'
                    f'{short_name}</div>',
                    unsafe_allow_html=True,
                )

# ── Save & Apply row ───────────────────────────────────────────────────────────
st.markdown("<br>", unsafe_allow_html=True)
save_col, status_col = st.columns([2, 5], gap="medium")

with save_col:
    n_sym = len(st.session_state.selected_symbols)
    save_disabled = n_sym == 0
    if st.button(
        "💾  Save & Apply for Today",
        type="primary",
        use_container_width=True,
        disabled=save_disabled,
        help="Write selection to session_config.json — main.py picks it up on next iteration",
    ):
        try:
            save_session_config(
                strategy=st.session_state.selected_strategy,
                symbols=sorted(st.session_state.selected_symbols),
            )
            st.session_state.save_success = True
        except ValueError as e:
            st.error(str(e))

with status_col:
    if save_disabled:
        st.warning("Select at least one stock to save the plan.")
    elif st.session_state.get("save_success"):
        strat_name = config.AVAILABLE_STRATEGIES[
            st.session_state.selected_strategy
        ]["short_name"]
        syms = sorted(st.session_state.selected_symbols)
        updated = get_updated_at() or datetime.now().strftime("%H:%M:%S")
        st.success(
            f"✓ Active plan saved at {updated}  ·  "
            f"**{strat_name}**  ·  {len(syms)} stocks: {', '.join(syms)}"
        )
    else:
        # Show what's currently saved on disk
        saved = load_session_config()
        if saved.get("updated_at"):
            strat_name = config.AVAILABLE_STRATEGIES.get(
                saved["strategy"], {}
            ).get("short_name", saved["strategy"])
            syms = saved["symbols"]
            st.info(
                f"Last saved: **{strat_name}**  ·  {', '.join(syms)}  "
                f"(saved at {saved['updated_at']})"
            )
        else:
            st.caption("No plan saved yet — configure above and click Save.")

st.divider()

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2: LIVE MONITORING
# ═══════════════════════════════════════════════════════════════════════════════

now_et = datetime.now(tz=ET)
st.markdown(
    f'<p class="section-title">Live Monitoring '
    f'<span style="color:#4b5563;font-weight:400;text-transform:none;letter-spacing:0;">'
    f'— {now_et.strftime("%H:%M:%S ET")}</span></p>',
    unsafe_allow_html=True,
)

# ── KPI cards ──────────────────────────────────────────────────────────────────
k1, k2, k3, k4, k5 = st.columns(5, gap="small")

vix = get_vix()
vg = VIXGate()
mkt_regime = vg.classify_market_regime(vix)
vix_color = {"low_vol": "kpi-green", "medium_vol": "kpi-yellow", "high_vol": "kpi-red"}[mkt_regime]
vix_mult = vg.get_position_size_multiplier(vix)

trades_df = _load_csv(config.TRADES_CSV)
today_str = now_et.strftime("%Y-%m-%d")
today_trades = pd.DataFrame()
if not trades_df.empty and "exit_date" in trades_df.columns:
    today_trades = trades_df[
        trades_df["exit_date"].astype(str).str.startswith(today_str, na=False)
    ]

total_pnl = (
    today_trades["pnl_dollars"].sum()
    if not today_trades.empty and "pnl_dollars" in today_trades.columns
    else 0.0
)
pnl_color = "kpi-green" if total_pnl >= 0 else "kpi-red"

orders_df = _load_csv(config.ORDERS_CSV)
n_orders_today = len(orders_df) if not orders_df.empty else 0

signals_df = _load_csv(config.SIGNALS_CSV)
n_signals_today = 0
if not signals_df.empty and "timestamp" in signals_df.columns and "direction" in signals_df.columns:
    n_signals_today = int(
        signals_df[
            signals_df["timestamp"].astype(str).str.startswith(today_str)
            & (signals_df["direction"].astype(str) != "0")
        ].shape[0]
    )

with k1:
    st.markdown(f"""
    <div class="kpi-box">
        <div class="kpi-label">VIX</div>
        <div class="kpi-value {vix_color}">{vix:.1f}</div>
        <div style="font-size:0.68rem;color:#6b7280;margin-top:4px;">
            {mkt_regime.replace('_',' ')} · {int(vix_mult*100)}% size
        </div>
    </div>""", unsafe_allow_html=True)

with k2:
    mkt_open = now_et.time() >= __import__("datetime").time(9, 30) and \
               now_et.time() <= __import__("datetime").time(15, 50) and \
               now_et.weekday() < 5
    status_txt = "OPEN" if mkt_open else "CLOSED"
    status_col = "kpi-green" if mkt_open else "kpi-red"
    st.markdown(f"""
    <div class="kpi-box">
        <div class="kpi-label">Market</div>
        <div class="kpi-value {status_col}">{status_txt}</div>
        <div style="font-size:0.68rem;color:#6b7280;margin-top:4px;">
            closes 3:50 PM ET
        </div>
    </div>""", unsafe_allow_html=True)

with k3:
    st.markdown(f"""
    <div class="kpi-box">
        <div class="kpi-label">Today's P&L</div>
        <div class="kpi-value {pnl_color}">${total_pnl:+,.2f}</div>
        <div style="font-size:0.68rem;color:#6b7280;margin-top:4px;">
            {len(today_trades)} closed trade{'s' if len(today_trades)!=1 else ''}
        </div>
    </div>""", unsafe_allow_html=True)

with k4:
    st.markdown(f"""
    <div class="kpi-box">
        <div class="kpi-label">Signals Today</div>
        <div class="kpi-value kpi-white">{n_signals_today}</div>
        <div style="font-size:0.68rem;color:#6b7280;margin-top:4px;">
            actionable (|Z| ≥ 2)
        </div>
    </div>""", unsafe_allow_html=True)

with k5:
    st.markdown(f"""
    <div class="kpi-box">
        <div class="kpi-label">Orders Today</div>
        <div class="kpi-value kpi-white">{n_orders_today}</div>
        <div style="font-size:0.68rem;color:#6b7280;margin-top:4px;">
            submitted to Alpaca
        </div>
    </div>""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ── Open positions + Closed trades ────────────────────────────────────────────
left_tbl, right_tbl = st.columns(2, gap="large")

with left_tbl:
    st.markdown('<p class="section-title">Open Positions</p>', unsafe_allow_html=True)
    if not orders_df.empty:
        show_cols = [c for c in ["symbol", "qty", "side", "entry_price", "timestamp"]
                     if c in orders_df.columns]
        st.dataframe(
            orders_df[show_cols].tail(20),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.markdown(
            '<div style="color:#4b5563;font-size:0.82rem;padding:12px 0;">No open positions</div>',
            unsafe_allow_html=True,
        )

with right_tbl:
    st.markdown('<p class="section-title">Today\'s Closed Trades</p>', unsafe_allow_html=True)
    if not today_trades.empty:
        show_cols = [c for c in [
            "symbol", "side", "entry_price", "exit_price",
            "pnl_pct", "hold_hours", "exit_reason",
        ] if c in today_trades.columns]
        styled = today_trades[show_cols].sort_values(
            "timestamp" if "timestamp" in today_trades.columns else show_cols[0],
            ascending=False,
        ).head(20)
        st.dataframe(styled, use_container_width=True, hide_index=True)
    else:
        st.markdown(
            '<div style="color:#4b5563;font-size:0.82rem;padding:12px 0;">No closed trades today</div>',
            unsafe_allow_html=True,
        )

st.markdown("<br>", unsafe_allow_html=True)

# ── Realized volatility chart ──────────────────────────────────────────────────
active_syms = sorted(st.session_state.selected_symbols)

_rv_tooltip = (
    "Realized Volatility measures how much a stock's price has actually moved "
    "over the past 20 trading days, expressed as an annualized percentage.\n\n"
    "Formula: RV = std(daily_returns, 20-day window) × √252\n\n"
    "Regimes used by this strategy:\n"
    "  • Low vol  ( < 15% ) — mean reversion is strong → full position size\n"
    "  • Med vol  (15–30%) — mean reversion is moderate → 60% size\n"
    "  • High vol ( ≥ 30% ) — momentum dominates, mean reversion skipped → 0 shares"
)
st.markdown(
    f'<p class="section-title">Realized Volatility — active universe (60-day) '
    f'<span title="{_rv_tooltip}" style="'
    f'cursor:help;color:#6b7280;font-size:0.78rem;'
    f'border-bottom:1px dotted #4b5563;padding-bottom:1px;">ℹ</span></p>',
    unsafe_allow_html=True,
)

rv_frames = [_realized_vol_series(s) for s in active_syms]
rv_frames = [df for df in rv_frames if not df.empty]

if rv_frames:
    rv_all = pd.concat(rv_frames, ignore_index=True)
    fig_rv = go.Figure()
    colors = ["#3b82f6", "#22c55e", "#f59e0b", "#f87171", "#a78bfa",
              "#34d399", "#fb923c", "#60a5fa", "#e879f9", "#facc15"]
    for i, sym in enumerate(active_syms):
        sub = rv_all[rv_all["symbol"] == sym]
        if sub.empty:
            continue
        fig_rv.add_trace(go.Scatter(
            x=sub["date"], y=(sub["rv"] * 100).round(2),
            mode="lines", name=sym,
            line=dict(color=colors[i % len(colors)], width=1.5),
        ))
    lo = config.REALIZED_VOL_LOW_THRESHOLD * 100
    hi = config.REALIZED_VOL_HIGH_THRESHOLD * 100
    fig_rv.add_hrect(y0=0,  y1=lo, fillcolor="#166534", opacity=0.10, line_width=0,
                     annotation_text="Low vol (mean reversion)", annotation_font_size=10,
                     annotation_font_color="#86efac")
    fig_rv.add_hrect(y0=lo, y1=hi, fillcolor="#713f12", opacity=0.10, line_width=0,
                     annotation_text="Med vol", annotation_font_size=10,
                     annotation_font_color="#fde68a")
    fig_rv.add_hrect(y0=hi, y1=120, fillcolor="#7f1d1d", opacity=0.10, line_width=0,
                     annotation_text="High vol (skip)", annotation_font_size=10,
                     annotation_font_color="#fca5a5")
    fig_rv.update_layout(
        paper_bgcolor="#13131f", plot_bgcolor="#0f0f1a",
        font_color="#9ca3af",
        yaxis=dict(title="Annualized Realized Vol (%)", gridcolor="#1f2937"),
        xaxis=dict(gridcolor="#1f2937"),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
        height=280, margin=dict(t=20, b=20, l=50, r=20),
    )
    st.plotly_chart(fig_rv, use_container_width=True)
else:
    st.caption("No volatility data available yet.")

# ── Z-score charts (one per active symbol) ────────────────────────────────────
st.markdown('<p class="section-title">Z-score — 5-min bars (last 100)</p>',
            unsafe_allow_html=True)

n_sym = len(active_syms)
if n_sym:
    ZCOLS = min(n_sym, 3)   # max 3 per row
    for row_start in range(0, n_sym, ZCOLS):
        row_syms = active_syms[row_start : row_start + ZCOLS]
        zcols = st.columns(len(row_syms), gap="small")
        for col, sym in zip(zcols, row_syms):
            with col:
                zdf = _zscore_series(sym)
                fig_z = go.Figure()
                if not zdf.empty:
                    fig_z.add_trace(go.Scatter(
                        x=zdf["ts"], y=zdf["z"].round(3),
                        mode="lines", name="Z-score",
                        line=dict(color="#60a5fa", width=1.2),
                    ))
                    thr = config.Z_SCORE_ENTRY_THRESHOLD
                    for y, color, label in [
                        (thr,  "#f87171", f"+{thr}σ short"),
                        (-thr, "#4ade80", f"-{thr}σ long"),
                        (0,    "#374151", ""),
                    ]:
                        fig_z.add_hline(
                            y=y, line_color=color,
                            line_dash="dash" if y != 0 else "solid",
                            line_width=1,
                            annotation_text=label,
                            annotation_font_size=9,
                            annotation_font_color=color,
                        )

                    # Mark signals
                    if not signals_df.empty and "timestamp" in signals_df.columns:
                        sig_sym = signals_df[signals_df["symbol"] == sym].copy()
                        sig_sym["timestamp"] = pd.to_datetime(
                            sig_sym["timestamp"], utc=True, errors="coerce"
                        )
                        for direction, color, symbol_marker in [
                            (1,  "#4ade80", "triangle-up"),
                            (-1, "#f87171", "triangle-down"),
                        ]:
                            sub = sig_sym[sig_sym.get("direction", pd.Series(dtype=int)) == direction]
                            if not sub.empty:
                                fig_z.add_trace(go.Scatter(
                                    x=sub["timestamp"],
                                    y=[0] * len(sub),
                                    mode="markers",
                                    marker=dict(symbol=symbol_marker, color=color, size=9),
                                    name="Long" if direction == 1 else "Short",
                                    showlegend=False,
                                ))

                fig_z.update_layout(
                    title=dict(text=sym, font=dict(size=13, color="#f3f4f6")),
                    paper_bgcolor="#13131f", plot_bgcolor="#0f0f1a",
                    font_color="#9ca3af",
                    yaxis=dict(gridcolor="#1f2937", zeroline=False),
                    xaxis=dict(gridcolor="#1f2937", showticklabels=False),
                    showlegend=False,
                    height=190,
                    margin=dict(t=35, b=10, l=40, r=10),
                )
                st.plotly_chart(fig_z, use_container_width=True)

st.markdown("<br>", unsafe_allow_html=True)

# ── Recent signal log ──────────────────────────────────────────────────────────
st.markdown('<p class="section-title">Recent Signals (last 20)</p>', unsafe_allow_html=True)
if not signals_df.empty:
    disp_cols = [c for c in [
        "timestamp", "symbol", "direction", "z_score", "regime", "confidence"
    ] if c in signals_df.columns]
    log_df = signals_df[disp_cols].tail(20).sort_index(ascending=False)
    # Colour the direction column
    st.dataframe(log_df, use_container_width=True, hide_index=True)
else:
    st.markdown(
        '<div style="color:#4b5563;font-size:0.82rem;padding:8px 0;">No signals logged yet — '
        'start main.py to begin signal generation.</div>',
        unsafe_allow_html=True,
    )

# ── Auto-refresh footer ────────────────────────────────────────────────────────
st.markdown("<br>", unsafe_allow_html=True)
st.markdown(
    f'<div style="text-align:center;font-size:0.68rem;color:#374151;">'
    f'Refreshes every {REFRESH_SECONDS}s · '
    f'{now_et.strftime("%Y-%m-%d %H:%M:%S ET")}</div>',
    unsafe_allow_html=True,
)

time.sleep(REFRESH_SECONDS)
st.rerun()
