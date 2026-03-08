"""
dashboard.py — All Streamlit rendering and Plotly chart construction.

Responsibilities
----------------
- Receive pre-computed data dicts from data_layer.py
- Render every visual element: tables, charts, metrics, panels
- No API calls. No calculations beyond display formatting.

Public functions
----------------
render_protocol_tab(protocol, rates, fetched_at)
render_arb_table(rates)
render_yield_calculator(rates)
"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from api import STABLECOINS, PROTOCOLS
from data_layer import (
    compute_utilization_curve,
    compute_arb_pairs,
    compute_net_yield,
)

# ─── COLOUR PALETTE (matches global CSS vars) ─────────────────────────────────
_C = {
    "bg":       "#0a0c10",
    "surface":  "#111318",
    "border":   "#1e2330",
    "accent":   "#00ff88",
    "accent2":  "#7c4dff",
    "accent3":  "#ff6b35",
    "text":     "#e2e8f0",
    "muted":    "#64748b",
    "positive": "#00ff88",
    "negative": "#ff4444",
    "warn":     "#ffaa00",
}


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _util_color(util_pct: float) -> str:
    if util_pct < 70:
        return _C["positive"]
    if util_pct < 85:
        return _C["warn"]
    return _C["negative"]


def _plotly_cfg() -> dict:
    return {"displayModeBar": False, "staticPlot": False}


# ─── ERROR / EMPTY STATES ─────────────────────────────────────────────────────

def _no_data_panel(protocol: str) -> None:
    st.markdown(f"""
    <div style="
        background: rgba(255,68,68,0.06);
        border: 1px solid rgba(255,68,68,0.25);
        border-radius: 12px;
        padding: 2rem;
        text-align: center;
        font-family: 'Space Mono', monospace;
    ">
        <div style="font-size:1.6rem; margin-bottom:0.5rem;">⚠</div>
        <div style="color:#ff4444; font-size:0.85rem; font-weight:700; margin-bottom:0.4rem;">
            {protocol} data unavailable
        </div>
        <div style="color:#64748b; font-size:0.72rem; line-height:1.6;">
            The {protocol} API did not return data this cycle.<br>
            Click <b>↺ Refresh Data</b> to retry.
        </div>
    </div>
    """, unsafe_allow_html=True)


# ─── RATES TABLE ──────────────────────────────────────────────────────────────

def _render_rates_table(proto_rates: dict, available: list[str]) -> None:
    """Plotly go.Table — Streamlit strips native HTML tables."""
    assets, supply_apys, borrow_apys, ltvs, utils = [], [], [], [], []
    util_colors = []

    for sym in available:
        d = proto_rates[sym]
        util_pct = d["utilization"] * 100
        assets.append(sym)
        supply_apys.append(f"{d['supply_apy']:.2f}%")
        borrow_apys.append(f"{d['borrow_apy']:.2f}%" if d["borrow_apy"] is not None else "N/A")
        ltvs.append(f"{d['ltv']}%")
        utils.append(f"{util_pct:.1f}%")
        util_colors.append(_util_color(util_pct))

    fig = go.Figure(go.Table(
        columnwidth=[70, 100, 110, 70, 100],
        header=dict(
            values=["<b>ASSET</b>", "<b>SUPPLY APY</b>",
                    "<b>BORROW APY</b>", "<b>MAX LTV</b>", "<b>UTILIZATION</b>"],
            fill_color=_C["border"],
            font=dict(color=_C["muted"], size=11, family="Space Mono"),
            align="left",
            height=36,
            line_color=_C["bg"],
        ),
        cells=dict(
            values=[assets, supply_apys, borrow_apys, ltvs, utils],
            fill_color=[[_C["surface"]] * len(assets)] * 5,
            font=dict(
                color=[
                    [_C["text"]]     * len(assets),
                    [_C["positive"]] * len(assets),
                    [_C["warn"]]     * len(assets),
                    [_C["accent2"]]  * len(assets),
                    util_colors,
                ],
                size=12,
                family="Space Mono",
            ),
            align="left",
            height=38,
            line_color=_C["border"],
        ),
    ))
    fig.update_layout(
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor=_C["bg"],
        plot_bgcolor=_C["bg"],
        height=60 + len(available) * 40,
    )
    st.plotly_chart(fig, use_container_width=True, config=_plotly_cfg())


# ─── UTILIZATION CURVE ────────────────────────────────────────────────────────

def _render_util_curve(proto_rates: dict, available: list[str], protocol: str) -> None:
    """Interactive utilization → rate curve with asset selector dropdown."""
    selected = st.selectbox(
        "Asset",
        available,
        key=f"curve_asset_{protocol}",
        label_visibility="collapsed",
    )

    d            = proto_rates[selected]
    current_util = d["utilization"]
    borrow_apy   = d["borrow_apy"]

    if borrow_apy is None:
        st.markdown(
            '<div class="info-box">Borrow rates not yet available — utilization curve unavailable.</div>',
            unsafe_allow_html=True,
        )
        return

    u_pct, borrow_curve, supply_curve = compute_utilization_curve(borrow_apy, current_util)
    current_u_pct = current_util * 100

    fig = go.Figure()

    # Kink zone: 80–90% (caution)
    fig.add_vrect(x0=80, x1=90,
                  fillcolor="rgba(255,170,0,0.07)",
                  layer="below", line_width=0,
                  annotation_text="↑ KINK 1", annotation_position="top left",
                  annotation_font=dict(color=_C["warn"], size=9, family="Space Mono"))

    # Kink zone: 90–100% (danger)
    fig.add_vrect(x0=90, x1=100,
                  fillcolor="rgba(255,68,68,0.07)",
                  layer="below", line_width=0,
                  annotation_text="↑ KINK 2", annotation_position="top left",
                  annotation_font=dict(color=_C["negative"], size=9, family="Space Mono"))

    # Supply curve
    fig.add_trace(go.Scatter(
        x=u_pct, y=supply_curve,
        mode="lines",
        name="Supply APY",
        line=dict(color=_C["positive"], width=2),
        hovertemplate="Util %{x:.0f}% → Supply %{y:.2f}%<extra></extra>",
    ))

    # Borrow curve
    fig.add_trace(go.Scatter(
        x=u_pct, y=borrow_curve,
        mode="lines",
        name="Borrow APY",
        line=dict(color=_C["warn"], width=2),
        hovertemplate="Util %{x:.0f}% → Borrow %{y:.2f}%<extra></extra>",
    ))

    # Shaded area between curves
    fig.add_trace(go.Scatter(
        x=list(u_pct) + list(u_pct[::-1]),
        y=list(borrow_curve) + list(supply_curve[::-1]),
        fill="toself",
        fillcolor="rgba(124,77,255,0.05)",
        line=dict(color="rgba(0,0,0,0)"),
        showlegend=False,
        hoverinfo="skip",
    ))

    # Current utilization marker
    fig.add_vline(
        x=current_u_pct,
        line=dict(color=_C["accent2"], width=1.5, dash="dash"),
        annotation_text=f" NOW {current_u_pct:.1f}%",
        annotation_position="top right",
        annotation_font=dict(color=_C["accent2"], size=9, family="Space Mono"),
    )

    fig.update_layout(
        height=260,
        paper_bgcolor=_C["bg"],
        plot_bgcolor=_C["surface"],
        font=dict(family="Space Mono", color=_C["muted"], size=10),
        xaxis=dict(
            title="Utilization %",
            showgrid=True, gridcolor=_C["border"],
            range=[0, 100],
            fixedrange=True,
        ),
        yaxis=dict(
            title="APY %",
            showgrid=True, gridcolor=_C["border"],
            fixedrange=True,
        ),
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(size=9),
            orientation="h",
            y=1.05,
        ),
        margin=dict(l=0, r=0, t=30, b=0),
        hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True, config=_plotly_cfg())


# ─── LTV PANEL ────────────────────────────────────────────────────────────────

def _render_ltv_panel(proto_rates: dict, available: list[str]) -> None:
    """Three-column LTV / liquidation / buffer gauge."""
    d      = proto_rates[available[0]]
    ltv    = d["ltv"]
    liq    = d["liq_threshold"]
    buffer = round(liq - ltv, 1)

    c1, c2, c3 = st.columns(3)
    for col, label, value, color, note in [
        (c1, "Max LTV",        f"{ltv}%",    _C["accent2"],  "borrow limit"),
        (c2, "Liq Threshold",  f"{liq}%",    _C["accent3"],  "liquidation trigger"),
        (c3, "Safety Buffer",  f"{buffer}%", _C["positive"], "breathing room"),
    ]:
        with col:
            st.markdown(f"""
            <div style="background:{_C['surface']};border:1px solid {_C['border']};
                        border-radius:12px;padding:1.2rem;">
                <div style="font-family:'Space Mono',monospace;font-size:0.65rem;
                            text-transform:uppercase;letter-spacing:0.1em;
                            color:{_C['muted']};">{label}</div>
                <div style="font-family:'Syne',sans-serif;font-size:1.9rem;
                            font-weight:800;color:{color};">{value}</div>
                <div style="font-family:'Space Mono',monospace;font-size:0.62rem;
                            color:{_C['muted']};margin-top:4px;">{note}</div>
            </div>
            """, unsafe_allow_html=True)


# ─── PROTOCOL TAB (public) ────────────────────────────────────────────────────

def render_protocol_tab(protocol: str, rates: dict, fetched_at: str, debug: dict[str, str] | None = None, debug_mode: bool = False) -> None:
    """
    Render the full content of one protocol tab:
    left column  → rates table + timestamp + LTV panel
    right column → utilization curve with asset selector
    """
    proto_rates = rates.get(protocol, {})
    available   = [s for s in STABLECOINS if proto_rates.get(s)]

    if not available:
        _no_data_panel(protocol)
        return

    # Protocol description
    blurb = {
        "Kamino":  "Largest Solana lender · Poly-linear IR curves · eMode for LST collateral",
        "JupLend": "Jupiter ecosystem · Unified liquidity layer · Up to 95% LTV on select pairs",
        "Drift":   "Hybrid lending + perps · Cross-margined collateral · Capital-efficient",
    }
    st.markdown(
        f'<div class="info-box">◈ {blurb[protocol]}</div>',
        unsafe_allow_html=True,
    )
    st.markdown("<br>", unsafe_allow_html=True)

    col_table, col_curve = st.columns([1, 1], gap="large")

    with col_table:
        st.markdown('<div class="section-heading">Rates & Utilization</div>',
                    unsafe_allow_html=True)
        # Timestamp
        st.markdown(
            f'<div style="font-family:\'Space Mono\',monospace;font-size:0.65rem;'
            f'color:{_C["muted"]};margin-top:-0.8rem;margin-bottom:0.6rem;">'
            f'<span class="live-dot"></span>last refreshed {fetched_at} · updates every 60s'
            f'</div>',
            unsafe_allow_html=True,
        )
        _render_rates_table(proto_rates, available)

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown('<div class="section-heading">DFDV SOL Collateral Params</div>',
                    unsafe_allow_html=True)
        _render_ltv_panel(proto_rates, available)

    with col_curve:
        st.markdown('<div class="section-heading">Utilization Curve</div>',
                    unsafe_allow_html=True)
        _render_util_curve(proto_rates, available, protocol)


# ─── ARB TABLE (public) ───────────────────────────────────────────────────────

def render_arb_table(rates: dict) -> None:
    """
    Cross-protocol arb spread table.
    For each stablecoin shows the best and second-best (borrow→supply) pairs.
    Positive net = profitable arb. Negative = best available (pick least bad).
    """
    rows = compute_arb_pairs(rates)

    if not rows:
        st.info("No cross-protocol data available yet.")
        return

    assets, ranks, b_protos, s_protos, b_apys, s_apys, nets = (
        [], [], [], [], [], [], []
    )
    net_colors = []

    for row in rows:
        assets.append(row["stable"])
        ranks.append(f"#{row['rank']}")
        b_protos.append(row["borrow_proto"])
        s_protos.append(row["supply_proto"])
        b_apys.append(f"{row['borrow_apy']:.2f}%")
        s_apys.append(f"{row['supply_apy']:.2f}%")
        net = row["net"]
        nets.append(f"{net:+.2f}%")
        net_colors.append(_C["positive"] if net > 0 else
                          _C["warn"]     if net > -2 else _C["negative"])

    fig = go.Figure(go.Table(
        columnwidth=[70, 35, 90, 90, 90, 90, 80],
        header=dict(
            values=["<b>ASSET</b>", "<b>#</b>",
                    "<b>BORROW FROM</b>", "<b>SUPPLY TO</b>",
                    "<b>BORROW APY</b>", "<b>SUPPLY APY</b>", "<b>NET SPREAD</b>"],
            fill_color=_C["border"],
            font=dict(color=_C["muted"], size=11, family="Space Mono"),
            align="left",
            height=36,
            line_color=_C["bg"],
        ),
        cells=dict(
            values=[assets, ranks, b_protos, s_protos, b_apys, s_apys, nets],
            fill_color=[[_C["surface"]] * len(rows)] * 7,
            font=dict(
                color=[
                    [_C["text"]]     * len(rows),
                    [_C["muted"]]    * len(rows),
                    [_C["warn"]]     * len(rows),
                    [_C["positive"]] * len(rows),
                    [_C["warn"]]     * len(rows),
                    [_C["positive"]] * len(rows),
                    net_colors,
                ],
                size=12,
                family="Space Mono",
            ),
            align="left",
            height=38,
            line_color=_C["border"],
        ),
    ))
    fig.update_layout(
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor=_C["bg"],
        height=60 + len(rows) * 40,
    )
    st.plotly_chart(fig, use_container_width=True, config=_plotly_cfg())

    st.markdown(
        f'<div style="font-family:\'Space Mono\',monospace;font-size:0.68rem;'
        f'color:{_C["muted"]};margin-top:-0.5rem;">'
        f'NET = Supply APY − Borrow APY · '
        f'<span style="color:{_C["positive"]}">Positive = profitable arb</span> · '
        f'<span style="color:{_C["warn"]}">Negative = cost of leverage (pick least bad)</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ─── YIELD CALCULATOR (public) ────────────────────────────────────────────────

def render_yield_calculator(rates: dict) -> None:
    """
    Interactive net yield estimator.
    Left: number inputs for staking APY, LTV, borrow/supply protocol+asset.
    Right: bar chart of yield components + metric tiles.
    """
    col_left, col_right = st.columns([1, 1], gap="large")

    with col_left:
        st.markdown(
            '<div class="info-box">'
            'Strategy: Deposit DFDV SOL → Borrow stablecoin → Supply it elsewhere.<br>'
            'Net = DFDV staking APY + (Supply APY − Borrow APY) × LTV used'
            '</div>',
            unsafe_allow_html=True,
        )
        st.markdown("<br>", unsafe_allow_html=True)

        dfdv_apy = st.number_input(
            "DFDV SOL Staking APY (%)",
            min_value=4.0, max_value=12.0, value=7.5, step=0.1, format="%.1f",
        )
        ltv_used = st.number_input(
            "LTV Used (%)",
            min_value=10, max_value=80, value=60, step=5,
        )

        st.markdown("<br>", unsafe_allow_html=True)

        # Borrow side
        st.markdown(
            f'<div style="font-family:\'Space Mono\',monospace;font-size:0.68rem;'
            f'color:{_C["warn"]};text-transform:uppercase;letter-spacing:0.08em;'
            f'margin-bottom:6px;">▼ Borrow From</div>',
            unsafe_allow_html=True,
        )
        bc1, bc2 = st.columns(2)
        with bc1:
            borrow_proto = st.selectbox("Borrow Protocol", PROTOCOLS, key="borrow_proto")
        with bc2:
            b_avail = [s for s in STABLECOINS if rates[borrow_proto].get(s)]
            if not b_avail:
                st.caption(f"No data for {borrow_proto}")
                return
            borrow_asset = st.selectbox("Borrow Asset", b_avail, key="borrow_asset")

        st.markdown("<br>", unsafe_allow_html=True)

        # Supply side
        st.markdown(
            f'<div style="font-family:\'Space Mono\',monospace;font-size:0.68rem;'
            f'color:{_C["positive"]};text-transform:uppercase;letter-spacing:0.08em;'
            f'margin-bottom:6px;">▲ Supply To</div>',
            unsafe_allow_html=True,
        )
        sc1, sc2 = st.columns(2)
        with sc1:
            supply_proto = st.selectbox("Supply Protocol", PROTOCOLS, key="supply_proto",
                                        index=PROTOCOLS.index(borrow_proto))
        with sc2:
            s_avail = [s for s in STABLECOINS if rates[supply_proto].get(s)]
            if not s_avail:
                st.caption(f"No data for {supply_proto}")
                return
            # Default to same asset as borrow if available
            default_idx = s_avail.index(borrow_asset) if borrow_asset in s_avail else 0
            supply_asset = st.selectbox("Supply Asset", s_avail, key="supply_asset",
                                        index=default_idx)

    # Pull live rates
    borrow_rate = rates[borrow_proto][borrow_asset]["borrow_apy"]
    supply_rate = rates[supply_proto][supply_asset]["supply_apy"]
    y           = compute_net_yield(dfdv_apy, ltv_used, borrow_rate, supply_rate)

    with col_right:
        st.markdown('<div class="section-heading">Yield Breakdown</div>',
                    unsafe_allow_html=True)

        components  = ["DFDV Staking", "Supply Earned", "Borrow Paid", "Net APY"]
        values      = [y["staking"], y["earned"], -y["paid"], y["net"]]
        bar_colors  = [
            _C["accent2"],
            _C["positive"],
            _C["negative"],
            _C["accent"] if y["net"] >= 0 else _C["negative"],
        ]

        fig2 = go.Figure()
        fig2.add_trace(go.Bar(
            x=components, y=values,
            marker_color=bar_colors,
            marker_line_width=0,
            text=[f"{v:+.2f}%" for v in values],
            textposition="auto",
            textfont=dict(family="Space Mono", size=11, color=_C["text"]),
        ))
        fig2.add_hline(y=0, line=dict(color="rgba(255,255,255,0.1)", width=1))
        fig2.update_layout(
            height=300,
            paper_bgcolor=_C["bg"],
            plot_bgcolor=_C["surface"],
            font=dict(family="Space Mono", color=_C["muted"], size=10),
            xaxis=dict(showgrid=False, tickfont=dict(size=10, color="#94a3b8"), fixedrange=True),
            yaxis=dict(
                showgrid=True, gridcolor=_C["border"], tickfont=dict(size=9),
                title="APY %", fixedrange=True,
                range=[min(values) * 1.4 - 1, max(values) * 1.4 + 1],
            ),
            margin=dict(l=0, r=0, t=10, b=0),
            showlegend=False,
            bargap=0.35,
        )
        st.plotly_chart(fig2, use_container_width=True, config=_plotly_cfg())

        m1, m2, m3 = st.columns(3)
        with m1:
            st.metric("Net Strategy APY", f"{y['net']:+.2f}%")
        with m2:
            st.metric("vs. Just Holding", f"{y['vs_hold']:+.2f}%")
        with m3:
            arrow = "↑" if y["arb_spread"] > 0 else "↓"
            st.metric("Arb Spread", f"{y['arb_spread']:+.2f}%",
                      f"{arrow} supply − borrow")