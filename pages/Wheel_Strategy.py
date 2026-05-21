"""
pages/Wheel_Strategy.py — Wheel options income strategy dashboard.
"""

from __future__ import annotations

from datetime import timezone

import pandas as pd
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
.metric-val   { font-size: 1.6rem; font-weight: 800; color: #f3f4f6; }
.metric-label { font-size: 0.70rem; color: #6b7280; text-transform: uppercase;
                letter-spacing: 0.09em; margin-top: 2px; }
.badge-csp { background: #1e3a5f; color: #93c5fd;
             border-radius: 4px; padding: 2px 8px; font-size: 0.72rem; font-weight: 700; }
.badge-cc  { background: #14532d; color: #86efac;
             border-radius: 4px; padding: 2px 8px; font-size: 0.72rem; font-weight: 700; }
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
        PAPER / RESEARCH
    </span>
</div>
""", unsafe_allow_html=True)


# ── How it works ───────────────────────────────────────────────────────────────

st.markdown('<p class="section-title">Strategy Overview</p>', unsafe_allow_html=True)

c1, arrow1, c2, arrow2, c3 = st.columns([4, 1, 4, 1, 4], gap="small")

with c1:
    st.markdown("""
    <div class="card" style="border-color:#1e3a5f;">
        <div style="font-size:1.5rem;font-weight:800;color:#1f2937;line-height:1;margin-bottom:6px;">01</div>
        <div style="font-size:0.95rem;font-weight:700;color:#f3f4f6;margin-bottom:6px;">
            <span class="badge-csp">CSP</span> Cash-Secured Put
        </div>
        <div style="font-size:0.80rem;color:#9ca3af;line-height:1.6;">
            Sell a ~25-delta put at 35 DTE. Reserve strike × 100 in cash.
            Close at 50% profit (max theta decay captured).<br><br>
            <span style="color:#4ade80;font-weight:600;">✓ Expires OTM</span> — keep premium, repeat.<br>
            <span style="color:#facc15;font-weight:600;">→ Assigned</span> — own shares at cost basis = strike − premium.
        </div>
    </div>
    """, unsafe_allow_html=True)

with arrow1:
    st.markdown("""
    <div style="display:flex;align-items:center;justify-content:center;
                height:100%;font-size:1.4rem;color:#374151;padding-top:40px;">→</div>
    """, unsafe_allow_html=True)

with c2:
    st.markdown("""
    <div class="card" style="border-color:#14532d;">
        <div style="font-size:1.5rem;font-weight:800;color:#1f2937;line-height:1;margin-bottom:6px;">02</div>
        <div style="font-size:0.95rem;font-weight:700;color:#f3f4f6;margin-bottom:6px;">
            <span class="badge-cc">CC</span> Covered Call
        </div>
        <div style="font-size:0.80rem;color:#9ca3af;line-height:1.6;">
            Sell a ~25-delta call above cost basis at 35 DTE. Shares collateralise.
            Close at 50% profit.<br><br>
            <span style="color:#4ade80;font-weight:600;">✓ Expires OTM</span> — keep premium, sell another CC.<br>
            <span style="color:#facc15;font-weight:600;">→ Called away</span> — shares sold at strike + premium kept.
        </div>
    </div>
    """, unsafe_allow_html=True)

with arrow2:
    st.markdown("""
    <div style="display:flex;align-items:center;justify-content:center;
                height:100%;font-size:1.4rem;color:#374151;padding-top:40px;">↺</div>
    """, unsafe_allow_html=True)

with c3:
    st.markdown("""
    <div class="card">
        <div style="font-size:1.5rem;font-weight:800;color:#374151;line-height:1;margin-bottom:6px;">↺</div>
        <div style="font-size:0.95rem;font-weight:700;color:#6b7280;margin-bottom:6px;">Reset</div>
        <div style="font-size:0.80rem;color:#6b7280;line-height:1.6;">
            After call-away, cash is freed and the cycle restarts.<br>
            Each full cycle spans roughly <b style="color:#f3f4f6;">60–90 days</b>
            and captures two rounds of premium.<br><br>
            Entry filter: <b style="color:#f3f4f6;">IV Rank ≥ 30</b>,
            annualised yield ≥ <b style="color:#f3f4f6;">20%</b>.
        </div>
    </div>
    """, unsafe_allow_html=True)


# ── Live CSP Scanner ────────────────────────────────────────────────────────────

st.markdown('<p class="section-title">CSP Scanner</p>', unsafe_allow_html=True)

st.info(
    "Uses **yfinance** daily data (no API key needed). "
    "IV approximated as HV30 × 1.15. Options priced with Black-Scholes.",
    icon="ℹ️",
)

col_btn, col_status = st.columns([1, 3])
with col_btn:
    run_scan = st.button("▶ Run Scan", use_container_width=True)
with col_status:
    st.markdown(
        '<span style="color:#6b7280;font-size:0.80rem;">'
        'Ranks all universe stocks by annualised premium yield. IV Rank ≥ 30 filter applied.</span>',
        unsafe_allow_html=True,
    )

if run_scan:
    with st.spinner("Fetching daily bars and computing signals…"):
        try:
            from strategies.wheel.scanner import run_scan as _run_scan
            result = _run_scan()
            st.session_state["wheel_scan_result"] = result
        except Exception as e:
            st.error(f"Scan failed: {e}")

result = st.session_state.get("wheel_scan_result")

if result is not None:
    ts_str = result.timestamp.astimezone(timezone.utc).strftime("%H:%M UTC")
    st.markdown(
        f'<p class="section-title">CSP Candidates · {len(result.signals)} · as of {ts_str}</p>',
        unsafe_allow_html=True,
    )
    if result.signals:
        rows = []
        for sig in result.signals:
            rows.append({
                "Symbol":       sig.symbol,
                "Price":        f"${sig.stock_price:,.2f}",
                "CSP Strike":   f"${sig.strike:,.2f}",
                "Premium":      f"${sig.premium:.2f}",
                "Delta":        f"{sig.delta:.2f}",
                "IV":           f"{sig.iv:.1%}",
                "IV Rank":      f"{sig.iv_rank:.0f}",
                "DTE":          sig.dte,
                "Ann. Yield":   f"{sig.ann_yield:.1%}",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.markdown(
            '<span style="color:#6b7280;font-size:0.82rem;">No candidates pass IV Rank ≥ 30 and yield ≥ 20% filters.</span>',
            unsafe_allow_html=True,
        )
    st.markdown(
        f'<span style="font-size:0.75rem;color:#4b5563;">'
        f'{result.n_symbols} symbols scanned · {len(result.skipped)} below filter threshold</span>',
        unsafe_allow_html=True,
    )


# ── Parameters reference ────────────────────────────────────────────────────────

with st.expander("Strategy Parameters", expanded=False):
    try:
        from strategies.wheel import wheel_config as cfg
        params = {
            "CSP target delta":      f"{cfg.CSP_TARGET_DELTA:.2f}",
            "CC target delta":       f"{cfg.CC_TARGET_DELTA:.2f}",
            "Target DTE":            cfg.TARGET_DTE,
            "Close at profit %":     f"{cfg.CLOSE_PROFIT_PCT:.0%}",
            "Min IV Rank":           cfg.MIN_IV_RANK,
            "Min annualised yield":  f"{cfg.MIN_ANN_YIELD:.0%}",
            "IV premium (HV mult)":  cfg.IV_PREMIUM,
            "Max position % equity": f"{cfg.MAX_POSITION_PCT:.0%}",
            "Max concurrent positions": cfg.MAX_POSITIONS,
            "Contracts per position":cfg.CONTRACTS,
            "Commission / contract": f"${cfg.COMMISSION_PER_CONTRACT:.2f}",
            "Slippage (bps)":        cfg.SLIPPAGE_BPS,
            "HV window (days)":      cfg.HIST_VOL_WINDOW,
            "Data source":           "yfinance (daily OHLCV)",
        }
        st.dataframe(
            pd.DataFrame(params.items(), columns=["Parameter", "Value"]),
            use_container_width=True, hide_index=True,
        )
    except Exception as e:
        st.error(f"Could not load config: {e}")


# ── Backtest results ─────────────────────────────────────────────────────────────

try:
    from strategies.wheel import wheel_config as _cfg
    _bt_report = _cfg.backtest_report_path()
    _bt_trades = _cfg.backtest_trades_path()
except Exception:
    _bt_report = None
    _bt_trades = None

if _bt_report and _bt_report.exists():
    st.markdown('<p class="section-title">Last Backtest Results</p>', unsafe_allow_html=True)
    try:
        bt = pd.read_csv(_bt_report)
        row = bt.iloc[0].to_dict()

        m1, m2, m3, m4, m5 = st.columns(5, gap="large")
        metric_pairs = [
            (f"{row.get('ann_return_pct', 0):.1f}%",  "Ann. Return"),
            (f"{row.get('sharpe_ratio', 0):.2f}",      "Sharpe Ratio"),
            (f"{row.get('max_drawdown_pct', 0):.1f}%", "Max Drawdown"),
            (f"{row.get('win_rate_pct', 0):.0f}%",     "Win Rate"),
            (f"{int(row.get('total_trades', 0))}",     "Total Trades"),
        ]
        for col, (val, label) in zip([m1, m2, m3, m4, m5], metric_pairs):
            with col:
                color = "#f87171" if val.startswith("-") else "#4ade80"
                if label == "Max Drawdown":
                    color = "#f87171"
                st.markdown(f"""
                <div class="card" style="text-align:center;padding:16px 10px;">
                    <div class="metric-val" style="color:{color};">{val}</div>
                    <div class="metric-label">{label}</div>
                </div>
                """, unsafe_allow_html=True)

        with st.expander("Full backtest metrics", expanded=False):
            st.dataframe(
                bt.T.reset_index().rename(columns={"index": "Metric", 0: "Value"}),
                use_container_width=True, hide_index=True,
            )
    except Exception as e:
        st.warning(f"Could not load backtest report: {e}")

    if _bt_trades and _bt_trades.exists():
        with st.expander("Trade log", expanded=False):
            try:
                trades_df = pd.read_csv(_bt_trades)
                st.dataframe(trades_df, use_container_width=True, hide_index=True)
            except Exception as e:
                st.warning(f"Could not load trade log: {e}")


# ── Footer ──────────────────────────────────────────────────────────────────────

st.markdown("<br>", unsafe_allow_html=True)
st.markdown("""
<div style="background:#13131f;border:1px solid #1f2937;border-radius:10px;
            padding:16px 20px;font-size:0.78rem;color:#6b7280;line-height:1.7;">
    <b style="color:#9ca3af;">Implementation status:</b>
    wheel_config.py <span style="color:#4ade80;">✓</span> ·
    strategy.py — Black-Scholes + delta <span style="color:#4ade80;">✓</span> ·
    universe.py — yfinance + IV rank <span style="color:#4ade80;">✓</span> ·
    scanner.py — CSP screener <span style="color:#4ade80;">✓</span> ·
    executor.py — Alpaca options API <span style="color:#4ade80;">✓</span> ·
    backtest.py — walk-forward simulation <span style="color:#4ade80;">✓</span>
    <br><br>
    <b style="color:#9ca3af;">Run backtest:</b>
    <code>PYTHONPATH=. python3 strategies/wheel/backtest.py --start 2022-01-01 --end 2024-12-31 --fetch</code>
    <br>
    <b style="color:#9ca3af;">Note:</b> Live execution requires an options-approved Alpaca account.
</div>
""", unsafe_allow_html=True)
