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
  nest_asyncio
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

# Sanity bounds for APY outputs (percentage, e.g. 5.0 = 5%)
_APY_MAX = 500.0


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


def _rates_from_spot_market(sm, symbol: str) -> tuple[float, float, float, dict]:
    """
    Compute (borrow_apy_pct, supply_apy_pct, utilization, raw_debug) from a
    SpotMarketAccount object.

    Returns a 4-tuple; raw_debug contains the intermediate on-chain values
    for diagnostic logging.
    """
    # Convert scaled balance shares → actual token amounts
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

    borrow_apy = _apr_to_apy_pct(borrow_apr)
    supply_apy = _apr_to_apy_pct(supply_apr)

    raw_debug = {
        "deposit_balance":              sm.deposit_balance,
        "borrow_balance":               sm.borrow_balance,
        "cumulative_deposit_interest":  sm.cumulative_deposit_interest,
        "cumulative_borrow_interest":   sm.cumulative_borrow_interest,
        "optimal_utilization_raw":      sm.optimal_utilization,
        "optimal_borrow_rate_raw":      sm.optimal_borrow_rate,
        "max_borrow_rate_raw":          sm.max_borrow_rate,
        "deposit_tokens":               deposit_tokens,
        "borrow_tokens":                borrow_tokens,
        "utilization_computed":         round(utilization, 6),
        "u_star":                       round(u_star, 6),
        "r_opt":                        round(r_opt, 6),
        "r_max":                        round(r_max, 6),
        "borrow_apr":                   round(borrow_apr, 6),
        "supply_apr":                   round(supply_apr, 6),
        "borrow_apy_pct":               borrow_apy,
        "supply_apy_pct":               supply_apy,
    }

    # Validate output sanity
    if not (0.0 <= borrow_apy <= _APY_MAX):
        raise ValueError(
            f"Drift {symbol}: borrow_apy {borrow_apy}% is out of range [0, {_APY_MAX}]. "
            f"raw={raw_debug}"
        )
    if not (0.0 <= supply_apy <= borrow_apy + 0.001):
        # supply can exceed borrow by tiny float rounding — allow 0.001 tolerance
        raise ValueError(
            f"Drift {symbol}: supply_apy {supply_apy}% > borrow_apy {borrow_apy}%. "
            f"raw={raw_debug}"
        )

    return borrow_apy, supply_apy, round(utilization, 6), raw_debug


# ─── ASYNC CORE ───────────────────────────────────────────────────────────────

async def _fetch_all_async() -> tuple[dict[str, dict], dict[str, str]]:
    """Connect read-only, fetch every stablecoin SpotMarketAccount, compute rates."""

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
    raw_debugs: dict[str, dict] = {}

    try:
        for symbol in STABLECOINS:
            market_idx = _STABLE_MARKET_INDEXES.get(symbol)
            if market_idx is None:
                continue  # not listed on Drift — skip silently

            try:
                sm = await get_spot_market_account(drift_client.program, market_idx)
                borrow_apy, supply_apy, utilization, raw = _rates_from_spot_market(sm, symbol)
                raw_debugs[symbol] = raw
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

    # Build a concise debug string from raw values for the first available market
    raw_summary = ""
    if raw_debugs:
        first_sym  = next(iter(raw_debugs))
        rd         = raw_debugs[first_sym]
        raw_summary = (
            f"{first_sym}: util={rd['utilization_computed']:.4f} "
            f"u*={rd['u_star']:.4f} r_opt={rd['r_opt']:.4f} r_max={rd['r_max']:.4f} "
            f"borrow_apy={rd['borrow_apy_pct']}% supply_apy={rd['supply_apy_pct']}%"
        )

    debug: dict[str, str] = {
        "rpc":               _RPC_URL,
        "source":            "DriftPy / SpotMarketAccount (on-chain)",
        "markets_fetched":   ", ".join(
            f"{s}={i}" for s, i in _STABLE_MARKET_INDEXES.items()
            if s in STABLECOINS
        ),
        "stablecoins_found": ", ".join(sorted(result.keys())) or "none",
        "status":            "ok" if result else "no_data",
        "raw_sample":        raw_summary,
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

    Handles both regular Python contexts and Streamlit's async event loop
    (via nest_asyncio when an event loop is already running).

    Raises ValueError if no stablecoin data could be retrieved.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # Running inside an existing event loop (e.g. newer Streamlit versions)
        try:
            import nest_asyncio
            nest_asyncio.apply()
        except ImportError:
            raise RuntimeError(
                "Drift fetcher needs 'nest_asyncio' when running inside Streamlit's "
                "async loop. Add 'nest_asyncio' to requirements.txt and reinstall."
            )
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, _fetch_all_async())
            return future.result()

    return asyncio.run(_fetch_all_async())
