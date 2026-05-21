"""
pages/Wheel_Strategy.py — Wheel Strategy overview page.
"""

import streamlit as st

st.set_page_config(
    page_title="Wheel Strategy",
    page_icon="⚙️",
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
.card {
    background: #13131f; border: 1px solid #1f2937;
    border-radius: 10px; padding: 20px 22px;
}
.step-num {
    font-size: 1.6rem; font-weight: 800; color: #1f2937;
    line-height: 1; margin-bottom: 6px;
}
.step-title { font-size: 0.95rem; font-weight: 700; color: #f3f4f6; margin-bottom: 6px; }
.step-desc  { font-size: 0.80rem; color: #9ca3af; line-height: 1.6; }
.outcome-good { color: #4ade80; font-weight: 600; }
.outcome-bad  { color: #facc15; font-weight: 600; }
.tag {
    display: inline-block; border-radius: 4px; padding: 2px 10px;
    font-size: 0.72rem; font-weight: 700; margin-right: 6px;
}
.tag-csp  { background: #1e3a5f; color: #93c5fd; }
.tag-cc   { background: #14532d; color: #86efac; }
.tag-soon { background: #1f2937; color: #6b7280; }
</style>
""", unsafe_allow_html=True)


# ── Header ─────────────────────────────────────────────────────────────────────

st.markdown("""
<div style="display:flex;justify-content:space-between;align-items:center;
            margin-bottom:6px;padding-bottom:12px;border-bottom:1px solid #1f2937;">
    <span style="font-size:1.2rem;font-weight:700;color:#f3f4f6;">
        ⚙️ Wheel Strategy
        <span style="font-size:0.80rem;font-weight:400;color:#6b7280;margin-left:8px;">
            Options income — Cash-Secured Puts + Covered Calls
        </span>
    </span>
    <span style="background:#1f2937;color:#6b7280;border-radius:6px;
                 padding:4px 12px;font-size:0.72rem;font-weight:700;letter-spacing:0.06em;">
        COMING SOON
    </span>
</div>
""", unsafe_allow_html=True)

st.markdown("""
<div style="font-size:0.84rem;color:#9ca3af;line-height:1.7;margin-bottom:8px;">
    The Wheel is a systematic options income strategy. You repeatedly sell premium on stocks
    you are willing to own, collecting cash every expiration cycle. It has two legs that
    alternate depending on whether you are assigned shares.
</div>
""", unsafe_allow_html=True)


# ── The Cycle ──────────────────────────────────────────────────────────────────

st.markdown('<p class="section-title">The Wheel Cycle</p>', unsafe_allow_html=True)

c1, arrow1, c2, arrow2, c3 = st.columns([4, 1, 4, 1, 4], gap="small")

with c1:
    st.markdown("""
    <div class="card" style="border-color:#1e3a5f;">
        <div class="step-num">01</div>
        <div class="step-title"><span class="tag tag-csp">CSP</span> Cash-Secured Put</div>
        <div class="step-desc">
            Sell a put option below the current stock price at your chosen strike.
            You receive premium immediately. The full cash to buy 100 shares
            (strike × 100) is reserved as collateral.<br><br>
            <span class="outcome-good">✓ Expires worthless</span> — keep premium, sell another CSP.<br>
            <span class="outcome-bad">→ Assigned</span> — you buy 100 shares at the strike. Move to leg 2.
        </div>
    </div>
    """, unsafe_allow_html=True)

with arrow1:
    st.markdown("""
    <div style="display:flex;align-items:center;justify-content:center;height:100%;
                font-size:1.4rem;color:#374151;padding-top:40px;">→</div>
    """, unsafe_allow_html=True)

with c2:
    st.markdown("""
    <div class="card" style="border-color:#14532d;">
        <div class="step-num">02</div>
        <div class="step-title"><span class="tag tag-cc">CC</span> Covered Call</div>
        <div class="step-desc">
            Sell a call option above your cost basis (strike paid − premium received).
            Your shares act as collateral — no extra cash needed.<br><br>
            <span class="outcome-good">✓ Expires worthless</span> — keep premium, sell another CC.<br>
            <span class="outcome-bad">→ Called away</span> — shares sold at the strike. Collect appreciation + premium. Wheel resets.
        </div>
    </div>
    """, unsafe_allow_html=True)

with arrow2:
    st.markdown("""
    <div style="display:flex;align-items:center;justify-content:center;height:100%;
                font-size:1.4rem;color:#374151;padding-top:40px;">↺</div>
    """, unsafe_allow_html=True)

with c3:
    st.markdown("""
    <div class="card">
        <div class="step-num" style="color:#374151;">↺</div>
        <div class="step-title" style="color:#6b7280;">Reset</div>
        <div class="step-desc">
            After the call is assigned, cash is freed up and the cycle restarts
            from leg 1 — sell a new cash-secured put on the same or a different stock.<br><br>
            Each full cycle (CSP → assignment → CC → called away) typically spans
            <b style="color:#f3f4f6;">60–90 calendar days</b> and two premium collections.
        </div>
    </div>
    """, unsafe_allow_html=True)


# ── Key Parameters ─────────────────────────────────────────────────────────────

st.markdown('<p class="section-title">Key Parameters</p>', unsafe_allow_html=True)

p1, p2, p3 = st.columns(3, gap="large")

with p1:
    st.markdown("""
    <div class="card">
        <div style="font-size:0.72rem;color:#6b7280;text-transform:uppercase;
                    letter-spacing:0.08em;margin-bottom:10px;">Strike Selection</div>
        <div style="font-size:0.82rem;color:#9ca3af;line-height:1.7;">
            <b style="color:#f3f4f6;">CSP delta: 0.20–0.30</b><br>
            Strike roughly 5–10% below spot. High enough to collect meaningful premium,
            low enough to avoid assignment on normal pullbacks.<br><br>
            <b style="color:#f3f4f6;">CC strike: above cost basis</b><br>
            Aim for a strike that, if called away, produces a net profit including
            both premiums collected.
        </div>
    </div>
    """, unsafe_allow_html=True)

with p2:
    st.markdown("""
    <div class="card">
        <div style="font-size:0.72rem;color:#6b7280;text-transform:uppercase;
                    letter-spacing:0.08em;margin-bottom:10px;">Expiration (DTE)</div>
        <div style="font-size:0.82rem;color:#9ca3af;line-height:1.7;">
            <b style="color:#f3f4f6;">Target: 30–45 DTE</b><br>
            This range sits in the steepest part of the theta decay curve — you sell
            when time value is highest and buy back (or let expire) when it's gone.<br><br>
            <b style="color:#f3f4f6;">Close at 50% profit</b><br>
            A common rule: buy back the option when you've captured 50% of max profit,
            then redeploy into the next cycle.
        </div>
    </div>
    """, unsafe_allow_html=True)

with p3:
    st.markdown("""
    <div class="card">
        <div style="font-size:0.72rem;color:#6b7280;text-transform:uppercase;
                    letter-spacing:0.08em;margin-bottom:10px;">Stock Selection</div>
        <div style="font-size:0.82rem;color:#9ca3af;line-height:1.7;">
            <b style="color:#f3f4f6;">High IV Rank (&gt; 30)</b><br>
            Elevated implied volatility means richer premiums. Sell when IV is high
            relative to its 1-year range (IV Rank or IV Percentile).<br><br>
            <b style="color:#f3f4f6;">Liquid options, strong underlying</b><br>
            Tight bid/ask spreads (AAPL, TSLA, NVDA, AMD).
            Only wheel stocks you'd be comfortable holding long-term.
        </div>
    </div>
    """, unsafe_allow_html=True)


# ── Risk / Reward ──────────────────────────────────────────────────────────────

st.markdown('<p class="section-title">Risk / Reward Profile</p>', unsafe_allow_html=True)

r1, r2 = st.columns(2, gap="large")

with r1:
    st.markdown("""
    <div class="card">
        <div style="font-size:0.72rem;color:#6b7280;text-transform:uppercase;
                    letter-spacing:0.08em;margin-bottom:12px;">Advantages</div>
        <ul style="font-size:0.82rem;color:#9ca3af;line-height:2;margin:0;padding-left:18px;">
            <li>Premium income every cycle regardless of direction</li>
            <li>Defined entry price — assignment only at your chosen strike</li>
            <li>Lower effective cost basis than buying shares outright</li>
            <li>Works in sideways and slightly bearish markets (unlike pure long equity)</li>
            <li>Simple, mechanical, no directional prediction needed</li>
        </ul>
    </div>
    """, unsafe_allow_html=True)

with r2:
    st.markdown("""
    <div class="card">
        <div style="font-size:0.72rem;color:#6b7280;text-transform:uppercase;
                    letter-spacing:0.08em;margin-bottom:12px;">Risks</div>
        <ul style="font-size:0.82rem;color:#9ca3af;line-height:2;margin:0;padding-left:18px;">
            <li>Assignment if stock drops sharply — paper loss on shares</li>
            <li>Capped upside — CC limits gains if stock rallies strongly</li>
            <li>IV crush after earnings — premium collapses, avoid holding through</li>
            <li>Capital-intensive — one contract requires strike × $100 in cash</li>
            <li>Slow recovery if assigned far below cost basis</li>
        </ul>
    </div>
    """, unsafe_allow_html=True)


# ── Footer ─────────────────────────────────────────────────────────────────────

st.markdown("<br>", unsafe_allow_html=True)
st.markdown("""
<div style="background:#13131f;border:1px solid #1f2937;border-radius:10px;
            padding:16px 20px;font-size:0.78rem;color:#6b7280;line-height:1.7;">
    <b style="color:#9ca3af;">Implementation roadmap:</b>
    wheel_config.py (parameters) · scanner (IV rank screener) ·
    executor (CSP/CC order submission via Alpaca options API) ·
    position tracker (assignment detection, cost basis tracking) ·
    dashboard (cycle progress, premium collected, annualised yield)
</div>
""", unsafe_allow_html=True)
