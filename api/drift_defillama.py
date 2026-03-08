"""
api/drift_defillama.py — Drift Protocol rate fetcher via DeFi Llama → Solana RPC fallback.

Approach
--------
1. Try DeFi Llama Yields API (https://yields.llama.fi/pools) for Drift on Solana.
   DeFi Llama currently does NOT index Drift lending pools, so this step will
   always fail and fall through to step 2.
2. Fall back to a lightweight direct Solana JSON-RPC fetch.
   Reads SpotMarketAccount binary data from the chain using `requests` only —
   no DriftPy / anchorpy / solana-py required.

Solana RPC decoding
-------------------
Account layout (data size = 776 bytes, confirmed against mainnet):

  offset   field                          type     precision
  ------   -----                          ----     ---------
  0        discriminator                  [u8;8]   —
  8        pubkey                         Pubkey   —
  40       oracle                         Pubkey   —
  72       mint                           Pubkey   —
  104      vault                          Pubkey   —
  136      name                           [u8;32]  ASCII, space-padded
  168…     historical_oracle_data         …        (skipped)
  …
  432      deposit_balance                u128     raw shares
  448      borrow_balance                 u128     raw shares
  464      cumulative_deposit_interest    u128     starts at CUM_PREC (1e10)
  480      cumulative_borrow_interest     u128     starts at CUM_PREC (1e10)
  …
  668      optimal_utilization            u32      / UTIL_PREC (1e6) → [0, 1]
  672      optimal_borrow_rate            u32      / RATE_PREC (1e6) → annual decimal
  676      max_borrow_rate                u32      / RATE_PREC (1e6) → annual decimal

Rate maths (same multi-kink formula as drift.py)
------------------------------------------------
  deposit_tokens = deposit_balance × cumulative_deposit_interest / 1e10
  borrow_tokens  = borrow_balance  × cumulative_borrow_interest  / 1e10
  utilization    = borrow_tokens / deposit_tokens
  borrow_APR     = multi_kink_curve(util, u_star, r_opt, r_max)
  supply_APR     = borrow_APR × util × (1 − 0.10 insurance-fund fee)
  APY            = (1 + APR/365)^365 − 1
"""

from __future__ import annotations

import base64
import math
import struct

import requests

from ._http import get_json
from .constants import STABLECOINS, LTV_PARAMS

# ─── DEFI LLAMA ───────────────────────────────────────────────────────────────

_DL_BASE    = "https://yields.llama.fi"
_DL_TIMEOUT = 20


def _try_defillama() -> dict[str, dict] | None:
    """
    Attempt to load Drift lending rates from DeFi Llama's Yields API.
    Returns a rates dict if data is found, or None if Drift lending is not indexed.

    Note: As of 2026-03, DeFi Llama does not index Drift lending pools.
    This function is kept so that if DeFi Llama adds Drift in the future,
    the dashboard will automatically pick it up.
    """
    try:
        body, _ = get_json("/pools", base=_DL_BASE, timeout=_DL_TIMEOUT, retries=1)
        all_pools: list = body if isinstance(body, list) else body.get("data", [])
    except Exception:
        return None

    best: dict[str, dict] = {}
    for pool in all_pools:
        if pool.get("project", "").lower() != "drift":
            continue
        if pool.get("chain", "").lower() != "solana":
            continue
        sym = (pool.get("symbol") or "").upper().strip()
        if sym not in STABLECOINS:
            continue
        supply_raw = pool.get("apy")
        if supply_raw is None:
            continue
        tvl = float(pool.get("tvlUsd") or 0)
        if sym not in best or tvl > float(best[sym].get("tvlUsd") or 0):
            best[sym] = pool

    if not best:
        return None

    result: dict[str, dict] = {}
    for sym, pool in best.items():
        supply_apy = round(float(pool["apy"]), 3)
        borrow_raw = pool.get("apyBorrow")
        borrow_apy = round(float(borrow_raw), 3) if borrow_raw is not None else None
        util_raw   = pool.get("utilization")
        util_frac  = round(min(max(float(util_raw) / 100, 0.0), 1.0), 4) if util_raw is not None else 0.0
        ltv_raw    = pool.get("ltv")
        ltv        = round(float(ltv_raw) * 100) if ltv_raw is not None else LTV_PARAMS["DriftDL"]["ltv"]
        result[sym] = {
            "supply_apy":    supply_apy,
            "borrow_apy":    borrow_apy,
            "utilization":   util_frac,
            "ltv":           ltv,
            "liq_threshold": LTV_PARAMS["DriftDL"]["liq"],
        }
    return result


# ─── SOLANA RPC (LIGHTWEIGHT FALLBACK) ────────────────────────────────────────

_RPC_URL  = "https://api.mainnet-beta.solana.com"
_PROGRAM  = "dRiftyHA39MWEi3m9aunc5MzRF1JYuBsbn6VPcn33UH"
_ACCT_LEN = 776  # SpotMarketAccount byte size on mainnet (confirmed)

# Byte offsets within the 776-byte SpotMarketAccount (verified on mainnet-beta)
_OFF_NAME     = 136   # [u8; 32] ASCII name, space/null padded
_OFF_DEP_BAL  = 432   # u128 deposit_balance (raw shares)
_OFF_BOR_BAL  = 448   # u128 borrow_balance  (raw shares)
_OFF_CUM_DEP  = 464   # u128 cumulative_deposit_interest (starts at 1e10)
_OFF_CUM_BOR  = 480   # u128 cumulative_borrow_interest  (starts at 1e10)
_OFF_OPT_UTIL = 668   # u32  optimal_utilization
_OFF_OPT_RATE = 672   # u32  optimal_borrow_rate
_OFF_MAX_RATE = 676   # u32  max_borrow_rate

# Precision constants (verified from raw on-chain values, 2026-03)
_CUM_PREC  = 10_000_000_000  # cumulative interest initial value
_UTIL_PREC = 1_000_000       # SPOT_MARKET_UTILIZATION_PRECISION
_RATE_PREC = 1_000_000       # SPOT_MARKET_RATE_PRECISION
_INS_FEE   = 0.10            # insurance-fund fee fraction

_APY_MAX   = 500.0

_RPC_TIMEOUT = 20


def _u128(data: bytes, off: int) -> int:
    return int.from_bytes(data[off : off + 16], "little")


def _u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def _rpc_post(payload: dict) -> dict:
    resp = requests.post(_RPC_URL, json=payload, timeout=_RPC_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _get_spot_market_accounts() -> list[tuple[str, bytes]]:
    """
    Return (pubkey, raw_bytes) for all SpotMarketAccount-sized accounts
    in the Drift program.  Filters by dataSize only (no auth required).
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getProgramAccounts",
        "params": [
            _PROGRAM,
            {"encoding": "base64", "filters": [{"dataSize": _ACCT_LEN}]},
        ],
    }
    result = _rpc_post(payload)
    if "error" in result:
        raise ValueError(f"Drift RPC error: {result['error']}")
    accounts = result.get("result") or []
    out = []
    for acct in accounts:
        raw = base64.b64decode(acct["account"]["data"][0])
        out.append((acct["pubkey"], raw))
    return out


def _borrow_apr(u: float, u_star: float, r_opt: float, r_max: float) -> float:
    """Multi-kink borrow APR (annual decimal). Same formula as api/drift.py."""
    if u <= 0 or u_star <= 0:
        return 0.0
    dr = r_max - r_opt
    if u <= u_star:
        return r_opt * (u / u_star)
    if u <= 0.85:
        return r_opt + dr * (50 / 1000) * ((u - u_star) / (0.85 - u_star))
    if u <= 0.90:
        return r_opt + dr * (50 + 100 * ((u - 0.85) / 0.05)) / 1000
    if u <= 0.95:
        return r_opt + dr * (50 + 100 + 150 * ((u - 0.90) / 0.05)) / 1000
    if u <= 0.99:
        return r_opt + dr * (50 + 100 + 150 + 200 * ((u - 0.95) / 0.04)) / 1000
    if u <= 0.995:
        return r_opt + dr * (50 + 100 + 150 + 200 + 250 * ((u - 0.99) / 0.005)) / 1000
    return r_opt + dr * (50 + 100 + 150 + 200 + 250 + 250 * ((u - 0.995) / 0.005)) / 1000


def _apr_to_apy_pct(apr: float) -> float:
    try:
        return round((math.pow(1.0 + apr / 365.0, 365) - 1.0) * 100, 3)
    except (ValueError, OverflowError):
        return round(apr * 100, 3)


def _decode_spot_market(data: bytes, sym: str) -> dict:
    dep_bal = _u128(data, _OFF_DEP_BAL)
    bor_bal = _u128(data, _OFF_BOR_BAL)
    cum_dep = _u128(data, _OFF_CUM_DEP)
    cum_bor = _u128(data, _OFF_CUM_BOR)

    deposit_tokens = dep_bal * cum_dep / _CUM_PREC
    borrow_tokens  = bor_bal * cum_bor / _CUM_PREC
    utilization    = min(borrow_tokens / deposit_tokens, 1.0) if deposit_tokens > 0 else 0.0

    u_star = _u32(data, _OFF_OPT_UTIL) / _UTIL_PREC
    r_opt  = _u32(data, _OFF_OPT_RATE) / _RATE_PREC
    r_max  = _u32(data, _OFF_MAX_RATE) / _RATE_PREC

    b_apr  = _borrow_apr(utilization, u_star, r_opt, r_max)
    s_apr  = b_apr * utilization * (1.0 - _INS_FEE)
    b_apy  = _apr_to_apy_pct(b_apr)
    s_apy  = _apr_to_apy_pct(s_apr)

    if not (0.0 <= b_apy <= _APY_MAX):
        raise ValueError(
            f"DriftDL {sym}: borrow_apy {b_apy:.3f}% out of range. "
            f"util={utilization:.4f} u*={u_star:.4f} r_opt={r_opt:.4f} r_max={r_max:.4f}"
        )

    return {
        "supply_apy":    s_apy,
        "borrow_apy":    b_apy,
        "utilization":   round(utilization, 4),
        "ltv":           LTV_PARAMS["DriftDL"]["ltv"],
        "liq_threshold": LTV_PARAMS["DriftDL"]["liq"],
    }


def _try_rpc() -> tuple[dict[str, dict], dict[str, str]]:
    """
    Read SpotMarket accounts directly from Solana mainnet via JSON-RPC.
    No DriftPy / anchorpy / solana-py required — only `requests`.
    """
    accounts = _get_spot_market_accounts()
    result: dict[str, dict] = {}
    errors: list[str] = []

    for pubkey, data in accounts:
        name_raw = data[_OFF_NAME : _OFF_NAME + 32]
        sym = name_raw.rstrip(b"\x00").rstrip(b" ").decode("ascii", errors="replace").strip().upper()
        if sym not in STABLECOINS:
            continue
        try:
            result[sym] = _decode_spot_market(data, sym)
        except Exception as exc:
            errors.append(f"{sym}({pubkey[:8]}…): {exc}")

    debug: dict[str, str] = {
        "source":            "Solana JSON-RPC (direct, no DriftPy)",
        "rpc":               _RPC_URL,
        "program":           _PROGRAM,
        "accounts_scanned":  str(len(accounts)),
        "stablecoins_found": ", ".join(sorted(result.keys())) or "none",
        "status":            "ok" if result else "no_data",
        "dl_note":           "DeFi Llama does not index Drift lending pools (2026-03)",
    }
    if errors:
        debug["errors"] = " | ".join(errors)
    if not result:
        raise ValueError(
            f"DriftDL (RPC fallback): no stablecoin data found. "
            f"Accounts scanned: {len(accounts)}. Errors: {errors}"
        )
    return result, debug


# ─── PUBLIC ENTRY POINT ───────────────────────────────────────────────────────

def fetch_drift_defillama_rates() -> tuple[dict[str, dict], dict[str, str]]:
    """
    Returns (rates_dict, debug_dict) using DeFi Llama if available, else Solana RPC.

    DeFi Llama does not currently index Drift lending pools (as of 2026-03),
    so this function always falls back to direct Solana RPC.  The DeFi Llama
    attempt is kept so the dashboard automatically benefits if DL adds Drift later.

    Matches the (rates_dict, debug_dict) interface of kamino.py / juplend.py.
    """
    # Step 1 — try DeFi Llama (will succeed automatically if they ever index Drift)
    dl_result = _try_defillama()
    if dl_result:
        debug: dict[str, str] = {
            "source":            "DeFi Llama Yields API",
            "url":               f"{_DL_BASE}/pools",
            "stablecoins_found": ", ".join(sorted(dl_result.keys())),
            "status":            "ok",
        }
        return dl_result, debug

    # Step 2 — DeFi Llama has no Drift lending data; fall back to Solana RPC
    return _try_rpc()
