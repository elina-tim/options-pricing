"""
app.py — Solana Lending Terminal · Entry point.

File structure
--------------
  api/
    __init__.py     Public API surface
    constants.py    Stablecoins, LTV params, protocol list
    kamino.py       Kamino Finance live rate fetcher
    juplend.py      Jupiter Lend live rate fetcher
    drift.py        Drift Protocol live rate fetcher
  data_layer.py     Aggregation + calculations + logging  (no UI)
  dashboard.py      All Streamlit / Plotly rendering      (no API calls)
  app.py            Page config, CSS, layout              ← this file

Run
---
  streamlit run app.py
"""

from datetime import datetime
import streamlit as st
from dotenv import load_dotenv
load_dotenv()

from data_layer import fetch_all_rates, compute_summary
from dashboard  import render_protocol_tab, render_arb_table, render_yield_calculator


# ─── PAGE CONFIG ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Solana Lending Terminal",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── GLOBAL CSS ───────────────────────────────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:ital,wght@0,400;0,700;1,400&family=Syne:wght@400;600;700;800&display=swap');

:root {
    --bg:       #0a0c10;
    --surface:  #111318;
    --border:   #1e2330;
    --accent:   #00ff88;
    --accent2:  #7c4dff;
    --accent3:  #ff6b35;
    --text:     #e2e8f0;
    --muted:    #64748b;
    --positive: #00ff88;
    --negative: #ff4444;
    --warn:     #ffaa00;
}

html, body, [class*="css"] {
    background-color: var(--bg) !important;
    color: var(--text) !important;
    font-family: 'Syne', sans-serif !important;
}
.stApp { background-color: var(--bg); }

.dashboard-header {
    border-bottom: 1px solid var(--border);
    padding-bottom: 1.5rem;
    margin-bottom: 2rem;
}
.dashboard-title {
    font-family: 'Syne', sans-serif;
    font-weight: 800;
    font-size: 2.2rem;
    letter-spacing: -0.03em;
    background: linear-gradient(135deg, #00ff88, #7c4dff);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}
.dashboard-subtitle {
    font-family: 'Space Mono', monospace;
    font-size: 0.75rem;
    color: var(--muted);
    letter-spacing: 0.1em;
    text-transform: uppercase;
    margin-top: 0.3rem;
}

div[data-testid="stMetric"] {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1rem;
}
div[data-testid="stMetricValue"] {
    font-family: 'Syne', sans-serif !important;
    font-weight: 700 !important;
    color: var(--accent) !important;
}
div[data-testid="stMetricLabel"] {
    font-family: 'Space Mono', monospace !important;
    font-size: 0.65rem !important;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--muted) !important;
}

.stTabs [data-baseweb="tab-list"] {
    background: var(--surface);
    border-radius: 10px;
    padding: 4px;
    border: 1px solid var(--border);
    gap: 4px;
}
.stTabs [data-baseweb="tab"] {
    background: transparent;
    border-radius: 8px;
    color: var(--muted) !important;
    font-family: 'Syne', sans-serif !important;
    font-weight: 600;
    font-size: 0.85rem;
    padding: 8px 20px;
    border: none !important;
}
.stTabs [aria-selected="true"] {
    background: var(--border) !important;
    color: var(--accent) !important;
}

.section-heading {
    font-family: 'Syne', sans-serif;
    font-size: 0.85rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.15em;
    color: var(--muted);
    margin-bottom: 1rem;
    display: flex;
    align-items: center;
    gap: 8px;
}
.section-heading::after {
    content: '';
    flex: 1;
    height: 1px;
    background: var(--border);
}

.info-box {
    background: rgba(124,77,255,0.08);
    border: 1px solid rgba(124,77,255,0.25);
    border-radius: 10px;
    padding: 1rem 1.2rem;
    font-size: 0.8rem;
    color: var(--muted);
    font-family: 'Space Mono', monospace;
    line-height: 1.6;
}

.debug-box {
    background: rgba(0,255,136,0.04);
    border: 1px solid rgba(0,255,136,0.15);
    border-radius: 8px;
    padding: 0.7rem 1rem;
    font-family: 'Space Mono', monospace;
    font-size: 0.68rem;
    color: #64748b;
    margin-top: 0.4rem;
    line-height: 1.8;
}
.debug-key   { color: #7c4dff; }
.debug-value { color: #00ff88; }

div[data-testid="stNumberInput"] label {
    font-family: 'Space Mono', monospace !important;
    font-size: 0.72rem !important;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--muted) !important;
}

.stSelectbox label {
    font-family: 'Space Mono', monospace !important;
    font-size: 0.7rem !important;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--muted) !important;
}

.live-dot {
    display: inline-block;
    width: 7px; height: 7px;
    border-radius: 50%;
    background: var(--accent);
    margin-right: 6px;
    animation: pulse 2s infinite;
}
@keyframes pulse {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0.3; }
}

::-webkit-scrollbar { width: 4px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
hr { border-color: var(--border) !important; opacity: 0.5; }
</style>
""", unsafe_allow_html=True)


# ─── SIDEBAR ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### ◈ Settings")
    debug_mode = st.toggle("Show debug info", value=False)
    st.caption("When on, shows API endpoint and fetch details under each protocol's rate table.")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main() -> None:
    now = datetime.now().strftime("%H:%M:%S UTC")

    # Header + Refresh button
    col_title, col_btn = st.columns([8, 1])
    with col_title:
        st.markdown(f"""
        <div class="dashboard-header">
            <div class="dashboard-title">◈ SOLANA LENDING TERMINAL</div>
            <div class="dashboard-subtitle">
                <span class="live-dot"></span>
                DFDV SOL COLLATERAL · KAMINO / JUPLEND / DRIFT · {now}
            </div>
        </div>
        """, unsafe_allow_html=True)
    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("↺ Refresh", type="secondary", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    # Fetch data
    with st.spinner("Fetching live rates…"):
        rates, fetched_at, errors, debug = fetch_all_rates()

    # Per-protocol error / stale banners
    hard_errors = {p: m for p, m in errors.items() if not m.startswith("[STALE:")}
    stale_warns = {p: m for p, m in errors.items() if m.startswith("[STALE:")}

    for proto, msg in stale_warns.items():
        st.warning(f"**{proto}** — showing cached data. {msg}")

    for proto, msg in hard_errors.items():
        st.error(
            f"**{proto}** — could not fetch live data. "
            f"The tab will show an error state until the API recovers.\n\n`{msg}`"
        )

    # All protocols hard-failed (stale protocols still have data, so don't block)
    if len(hard_errors) == 3:
        st.warning(
            "All three protocol APIs are currently unreachable. "
            "Click **↺ Refresh** to retry."
        )
        if st.button("↺ Refresh", type="primary"):
            st.cache_data.clear()
            st.rerun()
        return

    # Summary metrics
    st.markdown('<div class="section-heading">Live Summary</div>', unsafe_allow_html=True)
    s = compute_summary(rates)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Best Borrow Rate",  f"{s['best_borrow_rate']}%",
                  f"{s['best_borrow_proto']} · {s['best_borrow_asset']}")
    with c2:
        st.metric("Avg Borrow Rate",   f"{s['avg_borrow']}%",  "all protocols · all assets")
    with c3:
        st.metric("USDC Spread",       f"{s['usdc_spread']}%", "max − min borrow across protocols")
    with c4:
        st.metric("DFDV SOL Max LTV",  f"{s['dfdv_ltv']}%",   "best available collateral ratio")

    st.markdown("<br>", unsafe_allow_html=True)

    # Protocol tabs
    st.markdown('<div class="section-heading">Protocol Deep Dive</div>', unsafe_allow_html=True)
    tab1, tab2, tab3 = st.tabs(["  Kamino  ", "  JupLend  ", "  Drift  "])
    for tab, protocol in zip([tab1, tab2, tab3], ["Kamino", "JupLend", "Drift"]):
        with tab:
            render_protocol_tab(protocol, rates, fetched_at, debug.get(protocol, {}), debug_mode)

    # Arb table
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(
        '<div class="section-heading">Cross-Protocol Arb · Best Borrow → Best Supply</div>',
        unsafe_allow_html=True,
    )
    render_arb_table(rates)

    # Yield calculator
    st.markdown("<br><br>", unsafe_allow_html=True)
    st.markdown(
        '<div class="section-heading">Net Yield Estimator · DFDV SOL → Borrow → Supply</div>',
        unsafe_allow_html=True,
    )
    render_yield_calculator(rates)

    # Footer
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(f"""
    <div style="text-align:center;font-family:'Space Mono',monospace;font-size:0.65rem;
                color:#2d3748;padding-top:2rem;border-top:1px solid #1e2330;margin-top:1rem;">
        ◈ SOLANA LENDING TERMINAL · RATES REFRESH EVERY 60s ·
        INDICATIVE ONLY — NOT FINANCIAL ADVICE · {now}
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()