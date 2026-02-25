"""
api/drift.py — Drift Protocol live rate fetcher using DriftPy.

Source
------
Reads SpotMarketAccount directly on-chain via DriftPy (read-only,
no keypair required — uses a dummy wallet).

Maths — from official Drift docs (borrow-interest-rate page)
------------------------------------------------------------
Precision constants (from driftpy/constants/numeric_constants.py):
  SPOT_MARKET_UTILIZATION_PRECISION = 1_000_000_000   (1e9)
  SPOT_MARKET_RATE_PRECISION        = 1_000_000_000   (1e9)
  SPOT_MARKET_BALANCE_PRECISION     = 1_000_000_000   (1e9)
  cumulative interest accumulators start at               1e10

SpotMarketAccount fields used:
  deposit_balance             — scaled deposit shares
  borrow_balance              — scaled borrow shares
  cumulative_deposit_interest — interest accumulator for deposits
  cumulative_borrow_interest  — interest accumulator for borrows
  optimal_utilization         — U* in SPOT_MARKET_UTILIZATION_PRECISION
  optimal_borrow_rate         — R_opt in SPOT_MARKET_RATE_PRECISION (annual)
  max_borrow_rate             — R_max in SPOT_MARKET_RATE_PRECISION (annual)

Derived token amounts:
  deposit_tokens = deposit_balance * cumulative_deposit_interest / 1e10
  borrow_tokens  = borrow_balance  * cumulative_borrow_interest  / 1e10
  utilization    = borrow_tokens / deposit_tokens

Borrow APR — multi-kink model (see _borrow_apr_from_util).
Supply APR  = borrow_APR × utilization × (1 − 0.10 insurance fund fee).
APY         = (1 + APR/365)^365 − 1  (daily compounding).

Spot market indexes (mainnet-beta, Q1 2026)
-------------------------------------------
  0  USDC    6  PYUSD    8  USDS
  (USDG, USD1, CASH, PRIME not yet listed on Drift mainnet)

Add new entries to _STABLE_MARKET_INDEXES as Drift lists them.

Dependencies (add to requirements.txt):
  driftpy>=0.8.80
  anchorpy
  solana
"""

from __future__ import annotations

import asyncio
import math

from anchorpy import Wallet
from driftpy.drift_client import DriftClient
from driftpy.account_subscription_config import AccountSubscriptionConfig
from driftpy.accounts import get_spot_market_account
from solana.rpc.async_api import AsyncClient

from .constants import STABLECOINS, LTV_PARAMS

# ─── CONFIG ───────────────────────────────────────────────────────────────────

_RPC_URL = "https://api.mainnet-beta.solana.com"

# Maps our stablecoin symbols → Drift mainnet-beta spot market index
_STABLE_MARKET_INDEXES: dict[str, int] = {
    "USDC":  0,
    "PYUSD": 6,
    "USDS":  8,
    # Add here when Drift lists USDG, USD1, CASH, PRIME
}

# ─── ON-CHAIN PRECISION CONSTANTS ─────────────────────────────────────────────
# Source: driftpy/constants/numeric_constants.py

_UTIL_PREC = 1_000_000_000    # SPOT_MARKET_UTILIZATION_PRECISION
_RATE_PREC = 1_000_000_000    # SPOT_MARKET_RATE_PRECISION
_CUM_PREC  = 10_000_000_000   # cumulative interest initial value (1e10)

# Drift takes 10% of borrow interest for the insurance fund
_INS_FUND_FACTOR = 0.10


# ─── RATE MATHS ───────────────────────────────────────────────────────────────

def _borrow_apr_from_util(
    u: float,
    u_star: float,
    r_opt: float,
    r_max: float,
) -> float:
    """
    Multi-kink borrow APR (as a decimal, e.g. 0.05 = 5% annual).

    Parameters
    ----------
    u      – current utilization  [0, 1]
    u_star – optimal utilization  [0, 1]
    r_opt  – optimal borrow rate  [0, 1]  (annual decimal)
    r_max  – maximum borrow rate  [0, 1]  (annual decimal)

    Formula: https://docs.drift.trade/lend-borrow/borrow-interest-rate
    """
    if u <= 0 or u_star <= 0:
        return 0.0

    delta_r = r_max - r_opt

    if u <= u_star:
        return r_opt * (u / u_star)

    if u <= 0.85:
        return r_opt + delta_r * (50 / 1000) * ((u - u_star) / (0.85 - u_star))

    if u <= 0.90:
        return r_opt + delta_r * (50 + 100 * ((u - 0.85) / 0.05)) / 1000

    if u <= 0.95:
        return r_opt + delta_r * (50 + 100 + 150 * ((u - 0.90) / 0.05)) / 1000

    if u <= 0.99:
        return r_opt + delta_r * (50 + 100 + 150 + 200 * ((u - 0.95) / 0.04)) / 1000

    if u <= 0.995:
        return r_opt + delta_r * (50 + 100 + 150 + 200 + 250 * ((u - 0.99) / 0.005)) / 1000

    # u > 0.995
    return r_opt + delta_r * (50 + 100 + 150 + 200 + 250 + 250 * ((u - 0.995) / 0.005)) / 1000


def _apr_to_apy_pct(apr: float) -> float:
    """Convert annual decimal APR → APY percentage (daily compounding)."""
    try:
        return round((math.pow(1.0 + apr / 365.0, 365) - 1.0) * 100, 3)
    except (ValueError, OverflowError):
        return round(apr * 100, 3)


def _rates_from_spot_market(sm) -> tuple[float, float, float]:
    """
    Compute (borrow_apy_pct, supply_apy_pct, utilization) from a
    SpotMarketAccount object.

    utilization is returned as a float in [0, 1].
    """
    # Convert scaled balance shares → actual token amounts
    # Formula: tokens = balance * cumulativeInterest / CUM_PREC
    deposit_tokens = sm.deposit_balance * sm.cumulative_deposit_interest / _CUM_PREC
    borrow_tokens  = sm.borrow_balance  * sm.cumulative_borrow_interest  / _CUM_PREC

    utilization = (borrow_tokens / deposit_tokens) if deposit_tokens > 0 else 0.0
    utilization = min(max(utilization, 0.0), 1.0)

    # Decode on-chain rate parameters
    u_star = sm.optimal_utilization / _UTIL_PREC
    r_opt  = sm.optimal_borrow_rate / _RATE_PREC
    r_max  = sm.max_borrow_rate     / _RATE_PREC

    borrow_apr = _borrow_apr_from_util(utilization, u_star, r_opt, r_max)
    supply_apr = borrow_apr * utilization * (1.0 - _INS_FUND_FACTOR)

    return (
        _apr_to_apy_pct(borrow_apr),
        _apr_to_apy_pct(supply_apr),
        round(utilization, 6),
    )


# ─── ASYNC CORE ───────────────────────────────────────────────────────────────

async def _fetch_all_async() -> tuple[dict[str, dict], dict[str, str]]:
    """Connect read-only, fetch every stablecoin SpotMarketAccount, compute rates."""

    # Build index list for markets we care about
    target_indexes = [
        idx for sym, idx in _STABLE_MARKET_INDEXES.items()
        if sym in STABLECOINS
    ]

    connection   = AsyncClient(_RPC_URL)
    wallet       = Wallet.dummy()            # read-only — no signing needed

    drift_client = DriftClient(
        connection,
        wallet,
        "mainnet",
        spot_market_indexes=target_indexes,
        account_subscription=AccountSubscriptionConfig("cached"),
    )

    await drift_client.subscribe()

    result: dict[str, dict] = {}
    errors: list[str]       = []

    try:
        for symbol in STABLECOINS:
            market_idx = _STABLE_MARKET_INDEXES.get(symbol)
            if market_idx is None:
                continue  # not listed on Drift — skip silently

            try:
                sm = await get_spot_market_account(drift_client.program, market_idx)
                borrow_apy, supply_apy, utilization = _rates_from_spot_market(sm)
                result[symbol] = {
                    "supply_apy":    supply_apy,
                    "borrow_apy":    borrow_apy,
                    "utilization":   utilization,
                    "ltv":           LTV_PARAMS["Drift"]["ltv"],
                    "liq_threshold": LTV_PARAMS["Drift"]["liq"],
                }
            except Exception as exc:
                errors.append(f"{symbol}(idx={market_idx}): {exc}")
    finally:
        await drift_client.unsubscribe()
        await connection.close()

    debug: dict[str, str] = {
        "rpc":               _RPC_URL,
        "source":            "DriftPy / SpotMarketAccount (on-chain)",
        "markets_fetched":   ", ".join(
            f"{s}={i}" for s, i in _STABLE_MARKET_INDEXES.items()
            if s in STABLECOINS
        ),
        "stablecoins_found": ", ".join(sorted(result.keys())) or "none",
        "status":            "ok" if result else "no_data",
    }
    if errors:
        debug["errors"] = " | ".join(errors)

    if not result:
        raise ValueError(
            f"Drift DriftPy: no stablecoin data retrieved. Errors: {errors}"
        )

    return result, debug


# ─── PUBLIC SYNC ENTRY POINT ──────────────────────────────────────────────────

def fetch_drift_rates() -> tuple[dict[str, dict], dict[str, str]]:
    """
    Synchronous wrapper for Streamlit compatibility.
    Matches the (rates_dict, debug_dict) interface of kamino.py / juplend.py.

    Raises ValueError if no stablecoin data could be retrieved.
    """
    return asyncio.run(_fetch_all_async())