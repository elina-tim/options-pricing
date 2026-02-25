"""
api/constants.py — Shared constants for all API clients.

LTV_PARAMS: DFDV SOL collateral parameters per protocol.
  ltv  = maximum loan-to-value ratio (%), i.e. how much you can borrow
  liq  = liquidation threshold (%); position force-closed if LTV exceeds this

These values reflect on-chain risk parameters as of early 2026.
Update them if a protocol governance vote changes the values.
"""

# Stablecoins tracked across all three protocols
STABLECOINS: list[str] = [
    "USDC",
    "PYUSD",
    "USDG",
    "USD1",
    "CASH",
    "USDS",
    "PRIME",
]

# Risk parameters for DFDV SOL used as collateral on each protocol
LTV_PARAMS: dict[str, dict[str, int]] = {
    "Kamino":  {"ltv": 80, "liq": 85},
    "JupLend": {"ltv": 75, "liq": 82},
    "Drift":   {"ltv": 75, "liq": 80},
}

PROTOCOLS: list[str] = ["Kamino", "JupLend", "Drift"]