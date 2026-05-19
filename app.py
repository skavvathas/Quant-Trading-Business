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
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
[data-testid="stAppViewContainer"] { background: #0f0f1a; }
[data-testid="stHeader"]           { background: transparent; }
[data-testid="stSidebarNav"]       { display: none; }

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


# ── Strategy cards ─────────────────────────────────────────────────────────────

c1, c2, c3 = st.columns(3, gap="large")

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
    <div class="card dim">
        <div class="card-abbr" style="color:#374151;">MR</div>
        <div class="card-name">Mean Reversion</div>
        <div class="card-desc">Z-score entry gated by VIX regime and per-symbol realized vol</div>
        <span class="card-status grey">Coming soon</span>
    </div>
    """, unsafe_allow_html=True)

with c3:
    st.markdown("""
    <div class="card dim">
        <div class="card-abbr" style="color:#374151;">MOM</div>
        <div class="card-name">Intraday Momentum</div>
        <div class="card-desc">Trend-following with trailing stop anchored to VWAP</div>
        <span class="card-status grey">Coming soon</span>
    </div>
    """, unsafe_allow_html=True)
