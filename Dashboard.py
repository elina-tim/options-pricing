import streamlit as st
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# --- Page Config ---
st.set_page_config(
    page_title="Options Payoff Dashboard",
    page_icon="📈",
    layout="wide"
)

# --- Functions ---
def payoff_call(spot, strike):
    """Call option payoff"""
    return np.maximum(0, spot - strike)

def payoff_put(spot, strike):
    """Put option payoff"""
    return np.maximum(0, strike - spot)

def normal_pdf(x, mu=0, sigma=1):
    """Normal distribution PDF"""
    return (1 / (sigma * np.sqrt(2 * np.pi))) * np.exp(-0.5 * ((x - mu) / sigma) ** 2)

# --- Title ---
st.title("📈 Options Payoff Dashboard")
st.markdown("Interactive exploration of option payoffs and random distributions.")

# --- Sidebar Controls ---
st.sidebar.title("Parameters")

st.sidebar.subheader("Options")
strike = st.sidebar.slider("Strike Price (K)", min_value=80, max_value=120, value=100)
spot_min = st.sidebar.slider("Spot Range Min", min_value=50, max_value=95, value=80)
spot_max = st.sidebar.slider("Spot Range Max", min_value=105, max_value=150, value=120)

st.sidebar.subheader("Distributions")
n_points = st.sidebar.slider("Number of Points", min_value=100, max_value=5000, value=1000, step=100)
normal_mu = st.sidebar.slider("Normal Mean (μ)", min_value=-3.0, max_value=3.0, value=0.0, step=0.1)
normal_sigma = st.sidebar.slider("Normal Std Dev (σ)", min_value=0.1, max_value=3.0, value=1.0, step=0.1)

# --- Generate Data ---
np.random.seed(42)
normal_data = np.random.normal(loc=normal_mu, scale=normal_sigma, size=n_points)
uniform_data = np.random.uniform(low=-5, high=5, size=n_points)
spot_range = np.linspace(spot_min, spot_max, 200)
x_pdf = np.linspace(normal_mu - 4 * normal_sigma, normal_mu + 4 * normal_sigma, 300)

# --- KPI Metrics ---
col1, col2, col3, col4 = st.columns(4)
col1.metric("Strike Price", f"{strike}")
col2.metric("Normal Mean", f"{normal_data.mean():.4f}")
col3.metric("Normal Std Dev", f"{normal_data.std():.4f}")
col4.metric("Uniform Mean", f"{uniform_data.mean():.4f}")

st.divider()

# --- Charts ---
col1, col2 = st.columns(2)

with col1:
    st.subheader("Call & Put Payoffs")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=spot_range, y=payoff_call(spot_range, strike),
        name="Call", line=dict(color="green", width=2)
    ))
    fig.add_trace(go.Scatter(
        x=spot_range, y=payoff_put(spot_range, strike),
        name="Put", line=dict(color="red", width=2)
    ))
    fig.add_vline(x=strike, line_dash="dash", line_color="black", line_width=1,
                  annotation_text=f"K={strike}")
    fig.add_hline(y=0, line_dash="dash", line_color="black", line_width=0.5)
    fig.update_layout(
        xaxis_title="Spot Price",
        yaxis_title="Payoff",
        legend=dict(x=0.02, y=0.98),
        margin=dict(l=0, r=0, t=10, b=0)
    )
    st.plotly_chart(fig, use_container_width=True)

with col2:
    st.subheader("Normal Distribution")
    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=normal_data, nbinsx=50,
        histnorm="probability density",
        name="Samples",
        marker=dict(color="steelblue", line=dict(color="white", width=0.5))
    ))
    fig.add_trace(go.Scatter(
        x=x_pdf, y=normal_pdf(x_pdf, normal_mu, normal_sigma),
        name="PDF", line=dict(color="orange", width=2)
    ))
    fig.update_layout(
        xaxis_title="Value",
        yaxis_title="Density",
        margin=dict(l=0, r=0, t=10, b=0)
    )
    st.plotly_chart(fig, use_container_width=True)

col1, col2 = st.columns(2)

with col1:
    st.subheader("Uniform Distribution")
    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=uniform_data, nbinsx=50,
        histnorm="probability density",
        name="Uniform Samples",
        marker=dict(color="mediumpurple", line=dict(color="white", width=0.5))
    ))
    fig.update_layout(
        xaxis_title="Value",
        yaxis_title="Density",
        margin=dict(l=0, r=0, t=10, b=0)
    )
    st.plotly_chart(fig, use_container_width=True)

with col2:
    st.subheader("Call vs Put Payoff at Strike")
    strikes = np.arange(80, 121, 5)
    spot_fixed = 105
    call_payoffs = payoff_call(spot_fixed, strikes)
    put_payoffs = payoff_put(spot_fixed, strikes)

    fig = go.Figure()
    fig.add_trace(go.Bar(x=strikes, y=call_payoffs, name="Call Payoff", marker_color="green"))
    fig.add_trace(go.Bar(x=strikes, y=put_payoffs, name="Put Payoff", marker_color="red"))
    fig.update_layout(
        barmode="group",
        xaxis_title="Strike Price",
        yaxis_title=f"Payoff at Spot={spot_fixed}",
        margin=dict(l=0, r=0, t=10, b=0)
    )
    st.plotly_chart(fig, use_container_width=True)