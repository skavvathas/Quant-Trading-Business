"""
pages/Market_Overview.py — Real-time market overview.

Sections:
  1. Top 20 by Volume Today      (Alpaca snapshots over the universe)
  2. Top 20 by Market Cap        (yfinance, cached 1 h)
  3. Custom Watchlist            (NBIS, AMPX, NFLX, ELV, UNH)
  4. Stock Detail & Z-Score      (select any symbol → 20-day stats + chart)
"""

from datetime import datetime, time as dtime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytz
import streamlit as st

import config

ET = pytz.timezone("America/New_York")

WATCHLIST = ["NBIS", "AMPX", "NFLX", "ELV", "UNH"]

LARGE_CAPS = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "LLY", "AVGO",
    "TSLA", "WMT", "JPM", "V", "UNH", "XOM", "ORCL", "MA", "COST",
    "HD", "PG", "JNJ", "ABBV", "BAC", "KO", "CRM", "CVX", "MRK",
    "NFLX", "AMD", "ADBE", "ACN", "TMO", "PEP", "DIS", "CSCO", "WFC",
    "ABT", "MCD", "CAT", "GE", "INTU", "AXP", "IBM", "QCOM", "AMGN",
    "VZ", "TXN", "ISRG", "SPGI", "NOW", "PLTR",
]

st.set_page_config(
    page_title="Market Overview",
    page_icon="🌐",
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
    margin: 0 0 10px; padding-bottom: 6px;
    border-bottom: 1px solid #1f2937;
}
.up   { color: #4ade80; font-weight: 600; }
.down { color: #f87171; font-weight: 600; }
.neu  { color: #9ca3af; }
</style>
""", unsafe_allow_html=True)


# ── Data helpers ───────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def _alpaca_snapshots(symbols: list) -> dict:
    """Fetch Alpaca IEX snapshots → {sym: {price, change_pct, volume, vwap}}."""
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockSnapshotRequest
        client = StockHistoricalDataClient(
            api_key=config.ALPACA_API_KEY or None,
            secret_key=config.ALPACA_SECRET_KEY or None,
        )
        out = {}
        for i in range(0, len(symbols), 500):
            chunk = list(symbols[i: i + 500])
            try:
                snaps = client.get_stock_snapshot(
                    StockSnapshotRequest(symbol_or_symbols=chunk, feed="iex")
                )
                for sym, s in snaps.items():
                    try:
                        price      = float(s.latest_trade.price) if s.latest_trade else None
                        vol        = int(s.daily_bar.volume)     if s.daily_bar    else 0
                        vwap       = float(s.daily_bar.vwap)     if s.daily_bar and s.daily_bar.vwap else price
                        prev_close = float(s.prev_daily_bar.close) if s.prev_daily_bar else None
                        chg        = (price - prev_close) / prev_close * 100 if price and prev_close else 0.0
                        out[sym]   = {"price": price, "change_pct": round(chg, 2),
                                      "volume": vol, "vwap": vwap}
                    except Exception:
                        pass
            except Exception:
                pass
        return out
    except Exception:
        return {}


@st.cache_data(ttl=120)
def _yf_quotes(symbols: list) -> dict:
    """yfinance fallback: price + change% + volume for a symbol list."""
    import yfinance as yf
    out = {}
    try:
        raw = yf.download(symbols, period="2d", progress=False, auto_adjust=True)
        if raw.empty:
            return out
        close  = raw["Close"]
        volume = raw["Volume"]
        if isinstance(close.columns if hasattr(close, "columns") else pd.Index([]), pd.MultiIndex):
            close  = close
            volume = volume
        else:
            close  = pd.DataFrame(close)
            volume = pd.DataFrame(volume)
        for sym in symbols:
            try:
                c = close[sym].dropna() if sym in close.columns else pd.Series(dtype=float)
                v = volume[sym].dropna() if sym in volume.columns else pd.Series(dtype=float)
                if len(c) < 1:
                    continue
                price = float(c.iloc[-1])
                chg   = (c.iloc[-1] - c.iloc[-2]) / c.iloc[-2] * 100 if len(c) >= 2 else 0.0
                vol   = int(v.iloc[-1]) if len(v) >= 1 else 0
                out[sym] = {"price": price, "change_pct": round(chg, 2), "volume": vol, "vwap": None}
            except Exception:
                pass
    except Exception:
        pass
    return out


@st.cache_data(ttl=3600)
def _market_caps(symbols: list) -> dict:
    """Fetch market caps from yfinance fast_info (cached 1 h)."""
    import yfinance as yf
    caps = {}
    for sym in symbols:
        try:
            fi = yf.Ticker(sym).fast_info
            caps[sym] = getattr(fi, "market_cap", None) or 0
        except Exception:
            caps[sym] = 0
    return caps


@st.cache_data(ttl=300)
def _zscore_detail(symbol: str) -> dict:
    """Fetch 60 days of daily bars and compute 20-day rolling Z-scores."""
    import yfinance as yf
    try:
        df = yf.download(symbol, period="60d", progress=False, auto_adjust=True)
        if df.empty or len(df) < 5:
            return {}
        closes  = df["Close"].squeeze().dropna()
        volumes = df["Volume"].squeeze().dropna()
        mean20  = float(closes.rolling(20).mean().iloc[-1])
        std20   = float(closes.rolling(20).std().iloc[-1])
        price   = float(closes.iloc[-1])
        p_z     = (price - mean20) / std20 if std20 > 0 else 0.0

        mean_vol = float(volumes.rolling(20).mean().iloc[-1])
        std_vol  = float(volumes.rolling(20).std().iloc[-1])
        vol_now  = float(volumes.iloc[-1])
        v_z      = (vol_now - mean_vol) / std_vol if std_vol > 0 else 0.0

        return {
            "closes":       closes.tail(30),
            "volumes":      volumes.tail(30),
            "price_zscore": round(p_z, 2),
            "vol_zscore":   round(v_z, 2),
            "mean20":       round(mean20, 2),
            "std20":        round(std20, 2),
            "price":        round(price, 2),
            "mean_vol":     int(mean_vol),
            "vol_now":      int(vol_now),
        }
    except Exception:
        return {}


def _load_universe() -> list:
    import json
    path = config.DATA_DIR / "orb_universe.json"
    if path.exists():
        return json.loads(path.read_text())["symbols"]
    return LARGE_CAPS


# ── HTML table builder ─────────────────────────────────────────────────────────

def _mktcap_str(v: float) -> str:
    if not v:
        return "—"
    if v >= 1e12:
        return f"${v/1e12:.2f}T"
    if v >= 1e9:
        return f"${v/1e9:.1f}B"
    return f"${v/1e6:.0f}M"


def _chg_html(pct: float) -> str:
    cls = "up" if pct > 0 else "down" if pct < 0 else "neu"
    sign = "+" if pct > 0 else ""
    return f'<span class="{cls}">{sign}{pct:.2f}%</span>'


def _vol_str(v: int) -> str:
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v/1_000:.0f}K"
    return str(v)


def _snap_table(rows: list, show_mktcap: bool = False) -> str:
    cols = ["#", "Symbol", "Price", "Change", "Volume", "VWAP"]
    if show_mktcap:
        cols.insert(4, "Mkt Cap")
    hdr = "".join(
        f'<th style="text-align:left;padding:8px 12px;color:#9ca3af;font-size:0.70rem;'
        f'font-weight:600;letter-spacing:0.06em;border-bottom:1px solid #1f2937;">{c}</th>'
        for c in cols
    )
    body = ""
    for rank, r in enumerate(rows, 1):
        price_str = f"${r['price']:.2f}" if r.get("price") else "—"
        vwap_str  = f"${r['vwap']:.2f}" if r.get("vwap") else "—"
        vol_html  = _vol_str(r.get("volume") or 0)
        chg_html  = _chg_html(r.get("change_pct") or 0.0)
        row_cells = [
            f'<td style="padding:8px 12px;color:#4b5563;font-size:0.75rem;">{rank}</td>',
            f'<td style="padding:8px 12px;color:#f3f4f6;font-weight:700;">{r["symbol"]}</td>',
            f'<td style="padding:8px 12px;color:#d1d5db;">{price_str}</td>',
            f'<td style="padding:8px 12px;">{chg_html}</td>',
        ]
        if show_mktcap:
            row_cells.append(
                f'<td style="padding:8px 12px;color:#93c5fd;">{_mktcap_str(r.get("mktcap", 0))}</td>'
            )
        row_cells += [
            f'<td style="padding:8px 12px;color:#9ca3af;font-size:0.78rem;">{vol_html}</td>',
            f'<td style="padding:8px 12px;color:#9ca3af;font-size:0.78rem;">{vwap_str}</td>',
        ]
        body += f'<tr style="border-bottom:1px solid #1a1a2e;">{"".join(row_cells)}</tr>'

    return f"""
    <div style="background:#13131f;border:1px solid #1f2937;border-radius:10px;overflow:hidden;">
      <table style="width:100%;border-collapse:collapse;">
        <thead><tr style="background:#0f0f1a;">{hdr}</tr></thead>
        <tbody>{body}</tbody>
      </table>
    </div>"""


# ── Z-score panel ──────────────────────────────────────────────────────────────

def _render_zscore(symbol: str) -> None:
    with st.spinner(f"Loading {symbol} data…"):
        d = _zscore_detail(symbol)

    if not d:
        st.warning(f"Could not load data for {symbol}.")
        return

    pz = d["price_zscore"]
    vz = d["vol_zscore"]

    def _z_color(z):
        az = abs(z)
        if az > 2:  return "#f87171"
        if az > 1:  return "#facc15"
        return "#4ade80"

    def _z_label(z):
        az = abs(z)
        direction = "above" if z > 0 else "below"
        if az > 2:   return f"Extreme — {z:+.2f}σ {direction} 20-day mean"
        if az > 1:   return f"Elevated — {z:+.2f}σ {direction} 20-day mean"
        return f"Normal — {z:+.2f}σ from 20-day mean"

    # KPI strip
    k1, k2, k3, k4, k5 = st.columns(5, gap="small")
    kpis = [
        ("Price",        f"${d['price']:.2f}",       "#f3f4f6"),
        ("20d Mean",     f"${d['mean20']:.2f}",       "#9ca3af"),
        ("20d Std",      f"${d['std20']:.2f}",        "#9ca3af"),
        ("Price Z-Score",f"{pz:+.2f}σ",               _z_color(pz)),
        ("Vol Z-Score",  f"{vz:+.2f}σ",               _z_color(vz)),
    ]
    for col, (label, val, color) in zip([k1, k2, k3, k4, k5], kpis):
        col.markdown(
            f'<div style="background:#13131f;border:1px solid #1f2937;border-radius:8px;'
            f'padding:12px 14px;text-align:center;">'
            f'<div style="font-size:0.68rem;color:#6b7280;text-transform:uppercase;'
            f'letter-spacing:0.08em;">{label}</div>'
            f'<div style="font-size:1.3rem;font-weight:700;color:{color};margin-top:4px;">{val}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.markdown(
        f'<div style="margin:10px 0 4px;font-size:0.78rem;color:{_z_color(pz)};">'
        f'Price: {_z_label(pz)}</div>'
        f'<div style="font-size:0.78rem;color:{_z_color(vz)};">'
        f'Volume: {_z_label(vz)}</div>',
        unsafe_allow_html=True,
    )

    # Price chart with ±1σ / ±2σ bands
    closes = d["closes"]
    mean_line = [d["mean20"]] * len(closes)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=closes.index, y=[d["mean20"] + 2 * d["std20"]] * len(closes),
        line=dict(color="rgba(248,113,113,0.3)", width=1, dash="dot"),
        name="+2σ", showlegend=False,
    ))
    fig.add_trace(go.Scatter(
        x=closes.index, y=[d["mean20"] - 2 * d["std20"]] * len(closes),
        line=dict(color="rgba(248,113,113,0.3)", width=1, dash="dot"),
        fill="tonexty", fillcolor="rgba(248,113,113,0.04)",
        name="±2σ band",
    ))
    fig.add_trace(go.Scatter(
        x=closes.index, y=[d["mean20"] + d["std20"]] * len(closes),
        line=dict(color="rgba(250,204,21,0.4)", width=1, dash="dash"),
        name="+1σ", showlegend=False,
    ))
    fig.add_trace(go.Scatter(
        x=closes.index, y=[d["mean20"] - d["std20"]] * len(closes),
        line=dict(color="rgba(250,204,21,0.4)", width=1, dash="dash"),
        fill="tonexty", fillcolor="rgba(250,204,21,0.04)",
        name="±1σ band",
    ))
    fig.add_trace(go.Scatter(
        x=closes.index, y=mean_line,
        line=dict(color="#6b7280", width=1),
        name="20d mean",
    ))
    fig.add_trace(go.Scatter(
        x=closes.index, y=closes.values,
        line=dict(color="#60a5fa", width=2),
        name=symbol,
    ))
    fig.update_layout(
        paper_bgcolor="#13131f", plot_bgcolor="#0f0f1a",
        font_color="#9ca3af", height=260,
        margin=dict(t=10, b=30, l=60, r=20),
        yaxis=dict(gridcolor="#1f2937", tickprefix="$"),
        xaxis=dict(gridcolor="#1f2937"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, font_size=11),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Volume bar chart
    vols = d["volumes"]
    v_colors = ["#f87171" if v > d["mean_vol"] + 2 * (d["vol_now"] - d["mean_vol"]) * 0
                else "#4ade80" if v > d["mean_vol"] else "#374151"
                for v in vols.values]
    # simpler color: green if above mean, grey if below
    v_colors = ["#4ade80" if v > d["mean_vol"] else "#374151" for v in vols.values]
    fig2 = go.Figure()
    fig2.add_trace(go.Bar(x=vols.index, y=vols.values, marker_color=v_colors, name="Volume"))
    fig2.add_hline(y=d["mean_vol"], line_color="#facc15", line_dash="dash", line_width=1,
                   annotation_text="20d avg vol", annotation_font_color="#facc15",
                   annotation_font_size=10)
    fig2.update_layout(
        paper_bgcolor="#13131f", plot_bgcolor="#0f0f1a",
        font_color="#9ca3af", height=160,
        margin=dict(t=10, b=30, l=60, r=20),
        yaxis=dict(gridcolor="#1f2937"),
        xaxis=dict(gridcolor="#1f2937"),
        showlegend=False,
    )
    st.plotly_chart(fig2, use_container_width=True)

    st.caption(
        "Z-score = (current value − 20-day mean) ÷ 20-day std dev.  "
        "|Z| > 1 = elevated · |Z| > 2 = statistically extreme (outside 95% of historical range)."
    )


# ── Main render ────────────────────────────────────────────────────────────────

def render() -> None:
    now_et   = datetime.now(tz=ET)
    mkt_open = now_et.weekday() < 5 and dtime(9, 30) <= now_et.time() < dtime(16, 0)
    mkt_color = "#4ade80" if mkt_open else "#f87171"
    mkt_label = "MARKET OPEN" if mkt_open else "MARKET CLOSED"

    st.markdown(
        f"""<div style="display:flex;justify-content:space-between;align-items:center;
                        margin-bottom:14px;padding-bottom:12px;border-bottom:1px solid #1f2937;">
            <span style="font-size:1.2rem;font-weight:700;color:#f3f4f6;">
                🌐 Market Overview
            </span>
            <span style="display:flex;gap:20px;align-items:center;">
                <span style="font-size:0.72rem;color:#6b7280;">Data: Alpaca IEX + yfinance · refreshes every 60 s</span>
                <span style="color:{mkt_color};font-size:0.80rem;font-weight:700;">● {mkt_label}</span>
                <span style="font-size:0.78rem;color:#6b7280;">{now_et.strftime('%H:%M:%S ET')}</span>
            </span>
        </div>""",
        unsafe_allow_html=True,
    )

    # ── Load universe & snapshots ──────────────────────────────────────────────

    universe = _load_universe()
    all_syms  = list(set(universe + LARGE_CAPS + WATCHLIST))

    with st.spinner("Fetching market data…"):
        snaps = _alpaca_snapshots(all_syms)

    if not snaps:
        st.info("Alpaca snapshots unavailable — falling back to yfinance (slower).")
        snaps = _yf_quotes(all_syms)

    # ── Top 20 by Volume ──────────────────────────────────────────────────────

    st.markdown('<p class="section-title">Top 20 by Volume Today</p>', unsafe_allow_html=True)

    vol_rows = sorted(
        [{"symbol": s, **v} for s, v in snaps.items() if v.get("volume", 0) > 0],
        key=lambda r: r["volume"], reverse=True,
    )[:20]

    if vol_rows:
        st.markdown(_snap_table(vol_rows), unsafe_allow_html=True)
    else:
        st.warning("No volume data available. Markets may be closed.")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Top 20 by Market Cap ──────────────────────────────────────────────────

    st.markdown('<p class="section-title">Top 20 by Market Cap</p>', unsafe_allow_html=True)

    with st.spinner("Fetching market caps…"):
        caps = _market_caps(LARGE_CAPS)

    cap_rows = []
    for sym in LARGE_CAPS:
        snap = snaps.get(sym, {})
        cap_rows.append({
            "symbol":     sym,
            "price":      snap.get("price"),
            "change_pct": snap.get("change_pct", 0.0),
            "volume":     snap.get("volume", 0),
            "vwap":       snap.get("vwap"),
            "mktcap":     caps.get(sym, 0),
        })
    cap_rows = sorted(cap_rows, key=lambda r: r["mktcap"], reverse=True)[:20]

    st.markdown(_snap_table(cap_rows, show_mktcap=True), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Watchlist ─────────────────────────────────────────────────────────────

    st.markdown('<p class="section-title">Watchlist — NBIS · AMPX · NFLX · ELV · UNH</p>',
                unsafe_allow_html=True)

    watch_rows = []
    for sym in WATCHLIST:
        snap = snaps.get(sym, {})
        watch_rows.append({
            "symbol":     sym,
            "price":      snap.get("price"),
            "change_pct": snap.get("change_pct", 0.0),
            "volume":     snap.get("volume", 0),
            "vwap":       snap.get("vwap"),
        })
    st.markdown(_snap_table(watch_rows), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Z-Score Detail ────────────────────────────────────────────────────────

    st.markdown('<p class="section-title">Stock Detail & Z-Score</p>', unsafe_allow_html=True)

    all_displayed = list({r["symbol"] for r in vol_rows + cap_rows + watch_rows})
    all_displayed.sort()

    selected = st.selectbox(
        "Select a stock to analyse",
        options=all_displayed,
        index=all_displayed.index("NBIS") if "NBIS" in all_displayed else 0,
        label_visibility="collapsed",
    )

    if selected:
        _render_zscore(selected)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        f'<div style="text-align:center;font-size:0.68rem;color:#374151;">'
        f'Market Overview · {now_et.strftime("%Y-%m-%d %H:%M:%S ET")}</div>',
        unsafe_allow_html=True,
    )


render()
