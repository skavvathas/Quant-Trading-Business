"""
pages/AdaptiveTrend_Dashboard.py — AdaptiveTrend Crypto Strategy Dashboard.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="AdaptiveTrend — Crypto",
    page_icon="📈",
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
.metric-val   { font-size: 1.6rem; font-weight: 800; color: #f3f4f6; }
.metric-label { font-size: 0.70rem; color: #6b7280; text-transform: uppercase;
                letter-spacing: 0.09em; margin-top: 2px; }
.badge-long   { background: #14532d; color: #86efac; border-radius: 4px;
                padding: 2px 8px; font-size: 0.72rem; font-weight: 700; }
.badge-short  { background: #4c1d1d; color: #fca5a5; border-radius: 4px;
                padding: 2px 8px; font-size: 0.72rem; font-weight: 700; }
.badge-flat   { background: #1f2937; color: #6b7280; border-radius: 4px;
                padding: 2px 8px; font-size: 0.72rem; font-weight: 700; }
</style>
""", unsafe_allow_html=True)


# ── Header ──────────────────────────────────────────────────────────────────────

st.markdown("""
<div style="display:flex;justify-content:space-between;align-items:center;
            margin-bottom:6px;padding-bottom:12px;border-bottom:1px solid #1f2937;">
    <span style="font-size:1.2rem;font-weight:700;color:#f3f4f6;">
        📈 AdaptiveTrend
        <span style="font-size:0.80rem;font-weight:400;color:#6b7280;margin-left:8px;">
            Systematic crypto trend-following · 6-hour bars · 70/30 long-short
        </span>
    </span>
    <span style="background:#1f2937;color:#6b7280;border-radius:6px;
                 padding:4px 12px;font-size:0.72rem;font-weight:700;letter-spacing:0.06em;">
        PAPER / RESEARCH
    </span>
</div>
""", unsafe_allow_html=True)


# ── Paper benchmark metrics ─────────────────────────────────────────────────────

st.markdown('<p class="section-title">Paper Performance (Jan 2022 – Dec 2024)</p>',
            unsafe_allow_html=True)

m1, m2, m3, m4, m5 = st.columns(5, gap="large")
paper_metrics = [
    ("40.5%",  "Ann. Return"),
    ("2.41",   "Sharpe Ratio"),
    ("-12.7%", "Max Drawdown"),
    ("3.18",   "Calmar Ratio"),
    ("150+",   "Symbols Scanned"),
]
for col, (val, label) in zip([m1, m2, m3, m4, m5], paper_metrics):
    with col:
        color = "#4ade80" if not val.startswith("-") else "#f87171"
        st.markdown(f"""
        <div class="card" style="text-align:center;padding:16px 10px;">
            <div class="metric-val" style="color:{color};">{val}</div>
            <div class="metric-label">{label}</div>
        </div>
        """, unsafe_allow_html=True)


# ── Strategy overview ───────────────────────────────────────────────────────────

st.markdown('<p class="section-title">How It Works</p>', unsafe_allow_html=True)

h1, h2, h3 = st.columns(3, gap="large")
with h1:
    st.markdown("""
    <div class="card">
        <div style="font-size:0.72rem;color:#6b7280;text-transform:uppercase;
                    letter-spacing:0.08em;margin-bottom:10px;">① Signal</div>
        <div style="font-size:0.82rem;color:#9ca3af;line-height:1.7;">
            <b style="color:#f3f4f6;">Momentum over 5 days</b> (20 × 6h bars)<br>
            MOM = (P_t − P_{t−L}) / P_{t−L}<br><br>
            <b style="color:#f3f4f6;">Enter long</b> when MOM &gt; 2%<br>
            <b style="color:#f3f4f6;">Enter short</b> when MOM &lt; −2%<br><br>
            Filtered by trailing 90-day Sharpe:<br>
            Long ≥ 1.3 · Short ≥ 1.7
        </div>
    </div>
    """, unsafe_allow_html=True)

with h2:
    st.markdown("""
    <div class="card">
        <div style="font-size:0.72rem;color:#6b7280;text-transform:uppercase;
                    letter-spacing:0.08em;margin-bottom:10px;">② Trailing Stop</div>
        <div style="font-size:0.82rem;color:#9ca3af;line-height:1.7;">
            <b style="color:#f3f4f6;">ATR-based ratchet stop</b><br>
            S_t = max(S_{t−1}, P_t − α × ATR)<br>
            Optimal α = 2.5<br><br>
            Stop only moves in the direction of the trade —
            locking in profits as price extends.
            Close when price crosses the stop.
        </div>
    </div>
    """, unsafe_allow_html=True)

with h3:
    st.markdown("""
    <div class="card">
        <div style="font-size:0.72rem;color:#6b7280;text-transform:uppercase;
                    letter-spacing:0.08em;margin-bottom:10px;">③ Portfolio</div>
        <div style="font-size:0.82rem;color:#9ca3af;line-height:1.7;">
            <b style="color:#f3f4f6;">Monthly rebalance</b><br>
            Top 10 by dollar volume → long leg (70%)<br>
            Bottom 5 by momentum → short leg (30%)<br><br>
            Equal-weight within each leg.
            Data &amp; execution via <b style="color:#f3f4f6;">Alpaca</b> crypto spot API.
        </div>
    </div>
    """, unsafe_allow_html=True)


# ── Live scan section ────────────────────────────────────────────────────────────

st.markdown('<p class="section-title">Signal Scanner</p>', unsafe_allow_html=True)

st.info(
    "Uses **Alpaca crypto API** — same credentials as the ORB strategy. "
    "No additional setup needed if ALPACA_API_KEY is already in your .env.",
    icon="ℹ️",
)

col_btn, col_status = st.columns([1, 3])
with col_btn:
    run_scan = st.button("▶ Run Scan", use_container_width=True)
with col_status:
    st.markdown(
        '<span style="color:#6b7280;font-size:0.80rem;">'
        'Fetches live 6h OHLCV from Alpaca for all universe symbols.</span>',
        unsafe_allow_html=True,
    )

if run_scan:
    with st.spinner("Fetching 6h bars and computing signals…"):
        try:
            from strategies.adaptive_trend.scanner import run_scan as _run_scan
            result = _run_scan()
            st.session_state["at_scan_result"] = result
        except Exception as e:
            st.error(f"Scan failed: {e}")

result = st.session_state.get("at_scan_result")

if result is not None:
    ts_str = result.timestamp.astimezone(timezone.utc).strftime("%H:%M UTC")
    st.markdown(
        f'<p class="section-title">Long Signals · {len(result.long_signals)} · as of {ts_str}</p>',
        unsafe_allow_html=True,
    )
    if result.long_signals:
        rows = []
        for sig in result.long_signals:
            rows.append({
                "Symbol":        sig.symbol,
                "Direction":     "LONG",
                "MOM":           f"{sig.momentum:+.2%}",
                "ATR":           f"{sig.atr:.4f}",
                "Price":         f"{sig.price:.4f}",
                "Trail Stop":    f"{sig.trailing_stop:.4f}",
                "Sharpe (90d)":  f"{sig.sharpe:.2f}",
            })
        df_long = pd.DataFrame(rows)
        st.dataframe(df_long, use_container_width=True, hide_index=True)
    else:
        st.markdown('<span style="color:#6b7280;font-size:0.82rem;">No long signals passing filters.</span>',
                    unsafe_allow_html=True)

    st.markdown(
        f'<p class="section-title">Short Signals · {len(result.short_signals)}</p>',
        unsafe_allow_html=True,
    )
    if result.short_signals:
        rows = []
        for sig in result.short_signals:
            rows.append({
                "Symbol":        sig.symbol,
                "Direction":     "SHORT",
                "MOM":           f"{sig.momentum:+.2%}",
                "ATR":           f"{sig.atr:.4f}",
                "Price":         f"{sig.price:.4f}",
                "Trail Stop":    f"{sig.trailing_stop:.4f}",
                "Sharpe (90d)":  f"{sig.sharpe:.2f}",
            })
        df_short = pd.DataFrame(rows)
        st.dataframe(df_short, use_container_width=True, hide_index=True)
    else:
        st.markdown('<span style="color:#6b7280;font-size:0.82rem;">No short signals passing filters.</span>',
                    unsafe_allow_html=True)

    st.markdown(
        f'<span style="font-size:0.75rem;color:#4b5563;">'
        f'{result.bars_fetched} symbols fetched · {len(result.skipped)} skipped / no signal'
        f'</span>',
        unsafe_allow_html=True,
    )


# ── Parameters reference ─────────────────────────────────────────────────────────

with st.expander("Strategy Parameters", expanded=False):
    try:
        from strategies.adaptive_trend import adaptive_trend_config as cfg
        params = {
            "Lookback (bars)":           cfg.LOOKBACK_BARS,
            "Bar interval (hours)":      cfg.BAR_INTERVAL_H,
            "Entry threshold (MOM)":     f"{cfg.ENTRY_THRESHOLD:.1%}",
            "ATR multiplier (α)":        cfg.ALPHA,
            "ATR period":                cfg.ATR_PERIOD,
            "Long allocation (λ)":       f"{cfg.LAMBDA_LONG:.0%}",
            "Short allocation":          f"{cfg.LAMBDA_SHORT:.0%}",
            "Long universe size (K_L)":  cfg.K_LONG,
            "Short universe size (K_S)": cfg.K_SHORT,
            "Min Sharpe — long (γ_L)":   cfg.GAMMA_LONG,
            "Min Sharpe — short (γ_S)":  cfg.GAMMA_SHORT,
            "Rebalance frequency":       cfg.REBALANCE_FREQ,
            "Taker fee (bps)":           cfg.TAKER_FEE_BPS,
            "Slippage est. (bps)":       cfg.SLIPPAGE_BPS,
            "Data source":               "Alpaca Crypto (spot USD)",
        }
        df_params = pd.DataFrame(params.items(), columns=["Parameter", "Value"])
        st.dataframe(df_params, use_container_width=True, hide_index=True)
    except Exception as e:
        st.error(f"Could not load config: {e}")


# ── Backtest results (if available) ─────────────────────────────────────────────

_bt_report = cfg.backtest_report_path() if "cfg" in dir() else None
try:
    from strategies.adaptive_trend import adaptive_trend_config as _cfg
    _bt_report = _cfg.backtest_report_path()
except Exception:
    _bt_report = None

if _bt_report and _bt_report.exists():
    st.markdown('<p class="section-title">Last Backtest Results</p>', unsafe_allow_html=True)
    try:
        bt_df = pd.read_csv(_bt_report)
        st.dataframe(bt_df.T.reset_index().rename(columns={"index": "Metric", 0: "Value"}),
                     use_container_width=True, hide_index=True)
    except Exception as e:
        st.warning(f"Could not load backtest report: {e}")


# ── Roadmap ──────────────────────────────────────────────────────────────────────

st.markdown("<br>", unsafe_allow_html=True)
st.markdown("""
<div style="background:#13131f;border:1px solid #1f2937;border-radius:10px;
            padding:16px 20px;font-size:0.78rem;color:#6b7280;line-height:1.7;">
    <b style="color:#9ca3af;">Implementation status:</b>
    adaptive_trend_config.py <span style="color:#4ade80;">✓</span> ·
    strategy.py — MOM + trailing stop <span style="color:#4ade80;">✓</span> ·
    universe.py — Alpaca dollar-vol ranking <span style="color:#4ade80;">✓</span> ·
    scanner.py — signal generation <span style="color:#4ade80;">✓</span> ·
    executor.py — Alpaca spot orders <span style="color:#4ade80;">✓</span> ·
    backtest.py — walk-forward simulation <span style="color:#4ade80;">✓</span> ·
    live loop — position monitoring (planned)
    <br><br>
    <b style="color:#9ca3af;">Run backtest:</b>
    <code>PYTHONPATH=. python3 strategies/adaptive_trend/backtest.py --start 2023-01-01 --end 2024-12-31 --fetch</code>
</div>
""", unsafe_allow_html=True)
