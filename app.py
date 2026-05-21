"""
app.py — Strategy Hub landing page.

Run:
    streamlit run app.py
"""

import os
from datetime import datetime, time as dtime
from pathlib import Path

import pytz
import streamlit as st

import config

ET = pytz.timezone("America/New_York")

st.set_page_config(
    page_title="Trading Strategy Hub",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
[data-testid="stAppViewContainer"] { background: #0f0f1a; }
[data-testid="stHeader"]           { background: transparent; }

.card {
    background: #13131f;
    border: 1px solid #1f2937;
    border-radius: 12px;
    padding: 28px 24px 22px;
    cursor: pointer;
    transition: border-color 0.15s;
}
.card:hover        { border-color: #374151; }
.card.live         { border-color: #166534; }
.card.dim          { opacity: 0.35; cursor: default; }

.card-abbr   { font-size: 1.9rem; font-weight: 800; color: #f3f4f6;
               letter-spacing: -0.02em; margin-bottom: 4px; }
.card-name   { font-size: 0.72rem; color: #6b7280; text-transform: uppercase;
               letter-spacing: 0.09em; margin-bottom: 14px; }
.card-desc   { font-size: 0.82rem; color: #6b7280; line-height: 1.5; margin-bottom: 16px; }
.card-status { font-size: 0.70rem; font-weight: 700; }
.green { color: #4ade80; }
.grey  { color: #374151; }
</style>
""", unsafe_allow_html=True)


# ── Header ─────────────────────────────────────────────────────────────────────

now_et    = datetime.now(tz=ET)
mkt_open  = (
    now_et.weekday() < 5
    and now_et.time() >= dtime(9, 30)
    and now_et.time() <  dtime(16, 0)
)
mkt_color = "#4ade80" if mkt_open else "#f87171"
mkt_label = "MARKET OPEN" if mkt_open else "MARKET CLOSED"

st.markdown(
    f"""
    <div style="display:flex;justify-content:space-between;align-items:center;
                margin-bottom:40px;padding-bottom:16px;border-bottom:1px solid #1f2937;">
        <span style="font-size:1.4rem;font-weight:800;color:#f3f4f6;letter-spacing:-0.02em;">
            Strategy Hub
        </span>
        <span style="display:flex;gap:18px;align-items:center;">
            <span style="color:{mkt_color};font-size:0.78rem;font-weight:700;">● {mkt_label}</span>
            <span style="font-size:0.75rem;color:#4b5563;">{now_et.strftime('%H:%M ET')}</span>
        </span>
    </div>
    """,
    unsafe_allow_html=True,
)

# ── Market Overview button ─────────────────────────────────────────────────────

_mo_col, _ = st.columns([1, 3])
with _mo_col:
    st.page_link(
        "pages/Market_Overview.py",
        label="🌐  Market Overview",
        use_container_width=True,
    )

st.markdown("<br>", unsafe_allow_html=True)


# ── ORB live status ────────────────────────────────────────────────────────────

_pid = config.DATA_DIR / "orb_main.pid"
_orb_live = False
if _pid.exists():
    try:
        os.kill(int(_pid.read_text().strip()), 0)
        _orb_live = True
    except Exception:
        _pid.unlink(missing_ok=True)

orb_status_html = (
    '<span class="card-status green">● Trading live</span>'
    if _orb_live else
    '<span class="card-status grey">● Idle</span>'
)
orb_class = "card live" if _orb_live else "card"


# ── Quantitative Strategies ────────────────────────────────────────────────────

st.markdown(
    '<p style="font-size:0.70rem;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;'
    'color:#6b7280;margin-bottom:14px;padding-bottom:6px;border-bottom:1px solid #1f2937;">'
    '<h1 style="font-size:1.2rem;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:#f3f4f6;">Quantitative Strategies on Equities</h1>',
    unsafe_allow_html=True,
)

c1, c2, _spacer = st.columns([1, 1, 1], gap="large")

with c1:
    st.markdown(f"""
    <div class="{orb_class}">
        <div class="card-abbr">ORB</div>
        <div class="card-name">Opening Range Breakout</div>
        <div class="card-desc">Top 20 RelVol stocks · Stop entry at first candle high/low · EOD exit</div>
        {orb_status_html}
    </div>
    """, unsafe_allow_html=True)
    st.page_link("pages/ORB_Dashboard.py", label="Open dashboard →", use_container_width=True)

with c2:
    st.markdown("""
    <div class="card">
        <div class="card-abbr">ORB QQQ</div>
        <div class="card-name">ORB on QQQ / TQQQ</div>
        <div class="card-desc">
            5-min ORB on QQQ &amp; TQQQ only · 1% risk/trade · 10R target · Baseline + optimised
            variants · Paper: 33% ann. alpha (QQQ), 48% (TQQQ), beta ≈ 0.
        </div>
        <span class="card-status grey">● Backtest / Research</span>
    </div>
    """, unsafe_allow_html=True)
    st.page_link("pages/ORB_QQQ_Dashboard.py", label="Open dashboard →", use_container_width=True)

st.markdown("<br>", unsafe_allow_html=True)


# ── Options Strategies ─────────────────────────────────────────────────────────

st.markdown(
    '<p style="font-size:0.70rem;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;'
    'color:#6b7280;margin-bottom:14px;padding-bottom:6px;border-bottom:1px solid #1f2937;">'
    '<h1 style="font-size:1.2rem;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:#f3f4f6;">Options Strategies</h1>',
    unsafe_allow_html=True,
)

opt_c1, _opt_spacer = st.columns([1, 2], gap="large")

with opt_c1:
    st.markdown("""
    <div class="card">
        <div class="card-abbr">WHEEL</div>
        <div class="card-name">Wheel Strategy</div>
        <div class="card-desc">
            Sell cash-secured puts → take assignment → sell covered calls.
            Generates premium income on stocks you're willing to own.
        </div>
        <span class="card-status grey">● Strategy overview available</span>
    </div>
    """, unsafe_allow_html=True)
    st.page_link("pages/Wheel_Strategy.py", label="View strategy →", use_container_width=True)

st.markdown("<br>", unsafe_allow_html=True)


# ── Quantitative Strategies on Crypto ──────────────────────────────────────────

st.markdown(
    '<p style="font-size:0.70rem;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;'
    'color:#6b7280;margin-bottom:14px;padding-bottom:6px;border-bottom:1px solid #1f2937;">'
    '<h1 style="font-size:1.2rem;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:#f3f4f6;">Quantitative Strategies on Crypto</h1>',
    unsafe_allow_html=True,
)

crypto_c1, _crypto_spacer = st.columns([1, 2], gap="large")

with crypto_c1:
    st.markdown("""
    <div class="card">
        <div class="card-abbr">AT</div>
        <div class="card-name">AdaptiveTrend</div>
        <div class="card-desc">
            Systematic trend-following on 150+ crypto perpetuals · 6-hour bars ·
            70/30 long-short · ATR trailing stop · Monthly rebalance.
            Paper Sharpe 2.41, MDD −12.7% (2022–2024).
        </div>
        <span class="card-status grey">● Research / paper trading</span>
    </div>
    """, unsafe_allow_html=True)
    st.page_link("pages/AdaptiveTrend_Dashboard.py", label="Open dashboard →", use_container_width=True)
