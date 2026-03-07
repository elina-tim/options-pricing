"""
api/juplend.py — Jupiter Lend live rate fetcher.

Tries endpoints in order (first success wins):
  1. /lend/v1/earn/tokens  +  /lend/v1/borrow/vaults   (split supply / borrow)
  2. /lend/v2/earn/tokens  +  /lend/v2/borrow/vaults   (v2 variants)
  3. /lend/v1/markets                                   (combined legacy)
  4. /lend/v1/tokens                                    (alternate combined)

Each attempt is logged with HTTP status and response-key diagnostics so that
API changes are immediately visible in lending-YYYY-MM-DD.log.
"""

import os
import requests
from ._http import get_json
from .constants import STABLECOINS, LTV_PARAMS

_BASE    = "https://api.jup.ag"
_API_KEY = os.environ.get("JUPITER_API_KEY", "")
_HEADERS = {"x-api-key": _API_KEY} if _API_KEY else {}
_TIMEOUT = 20          # increased from 12 s


def _to_pct(v) -> float | None:
    if v is None:
        return None
    v = float(v)
    return round(v * 100 if abs(v) < 1 else v, 3)


def _get(path: str) -> tuple[list | dict, str]:
    """Wrapper that uses the shared retry helper."""
    return get_json(path, base=_BASE, timeout=_TIMEOUT, retries=2, backoff=1.0, headers=_HEADERS)


def fetch_juplend_rates() -> tuple[dict[str, dict], dict[str, str]]:
    """
    Returns
    -------
    rates   dict[symbol] -> rate_dict
    debug   dict[str, str] of diagnostic key/values:
              endpoint_used, url, stablecoins_found, status, attempt
    """
    attempts_log: list[str] = []
    last_err: Exception | None = None

    for attempt, (label, fn) in enumerate([
        ("v1/earn+borrow", _from_earn_and_borrow_v1),
        ("v2/earn+borrow", _from_earn_and_borrow_v2),
        ("v1/markets",     _from_markets),
        ("v1/tokens",      _from_tokens),
    ], start=1):
        try:
            result, debug = fn()
            debug["attempt"]      = str(attempt)
            debug["attempts_log"] = " | ".join(attempts_log) if attempts_log else "first try"
            return result, debug
        except Exception as exc:
            short = str(exc)[:200]
            attempts_log.append(f"[{label}] {short}")
            last_err = exc

    # All endpoints exhausted — build a rich error
    full_log = " || ".join(attempts_log)
    raise ValueError(f"JupLend: all endpoints failed. Details: {full_log}")


# ─── ENDPOINT IMPLEMENTATIONS ─────────────────────────────────────────────────

def _parse_earn_borrow(earn_list, borrow_list, label: str) -> tuple[dict, dict]:
    """
    Shared parser for earn + borrow split endpoints (v1 and v2).
    Returns (result_dict, debug_dict) or raises ValueError if no data.
    """
    supply_map: dict[str, float] = {}
    # borrow data embedded in the earn endpoint (fallback if borrow vaults unavailable)
    borrow_earn_map: dict[str, dict] = {}

    for t in earn_list:
        # Symbol location varies by API version: top-level 'symbol'/'name', or nested under 'asset'
        asset = t.get("asset") or {}
        sym = (
            t.get("symbol") or t.get("tokenSymbol") or t.get("name")
            or asset.get("symbol") or asset.get("name") or asset.get("ticker")
            or ""
        ).upper().strip()
        if sym not in STABLECOINS:
            continue

        # New API: totalRate = base supply APY + rewards; liquiditySupplyData has per-asset breakdown
        liquidity = t.get("liquiditySupplyData") or {}
        apy = _to_pct(
            t.get("supplyAPY") or t.get("depositAPY") or t.get("apy")
            or t.get("supplyApy") or t.get("lendApy")
            or t.get("supplyRate") or t.get("depositRate") or t.get("lendingRate")
            or t.get("totalRate")
            or liquidity.get("supplyRate") or liquidity.get("supplyAPY") or liquidity.get("apy")
        )
        if apy is not None:
            supply_map[sym] = apy

        # Some earn endpoints also carry borrow rates per asset
        borrow_apy = _to_pct(
            t.get("borrowAPY") or t.get("borrowInterestAPY")
            or t.get("borrowApy") or t.get("borrowRate")
            or t.get("borrowInterestRate") or t.get("borrowingRate")
            or liquidity.get("borrowRate") or liquidity.get("borrowAPY")
        )
        util = float(
            t.get("utilization") or t.get("utilizationRate")
            or liquidity.get("utilization") or liquidity.get("utilizationRate") or 0
        )
        if util > 1:
            util /= 100
        if borrow_apy is not None:
            borrow_earn_map[sym] = {"borrow_apy": borrow_apy, "utilization": round(util, 4)}

    borrow_map: dict[str, dict] = {}
    for v in borrow_list:
        sym = (
            v.get("symbol") or v.get("tokenSymbol")
            or v.get("debtToken", {}).get("symbol") or ""
        ).upper().strip()
        if sym not in STABLECOINS:
            continue
        apy  = _to_pct(
            v.get("borrowAPY") or v.get("borrowInterestAPY") or v.get("rate")
            or v.get("borrowApy") or v.get("borrowRate")
            or v.get("borrowInterestRate") or v.get("borrowingRate")
        )
        util = float(v.get("utilization") or v.get("utilizationRate") or 0)
        if util > 1:
            util /= 100
        if apy is not None:
            borrow_map[sym] = {"borrow_apy": apy, "utilization": round(util, 4)}

    # Prefer dedicated borrow endpoint; fall back to rates embedded in earn endpoint
    merged_borrow = {**borrow_earn_map, **borrow_map}

    result: dict[str, dict] = {}
    for sym in STABLECOINS:
        if sym in supply_map and sym in merged_borrow:
            result[sym] = {
                "supply_apy":    supply_map[sym],
                "borrow_apy":    merged_borrow[sym]["borrow_apy"],
                "utilization":   merged_borrow[sym]["utilization"],
                "ltv":           LTV_PARAMS["JupLend"]["ltv"],
                "liq_threshold": LTV_PARAMS["JupLend"]["liq"],
            }

    if not result:
        # Emit top-level keys so we can diagnose field-name changes
        earn_keys   = list({k for t in earn_list[:3]   for k in t})
        borrow_keys = list({k for v in borrow_list[:3] for k in v})
        raise ValueError(
            f"JupLend {label}: no matching stablecoin data. "
            f"earn keys={earn_keys} borrow keys={borrow_keys} "
            f"supply_map={list(supply_map)} merged_borrow={list(merged_borrow)}"
        )

    return result, result  # second return is a placeholder; caller builds debug


def _from_earn_and_borrow_v1() -> tuple[dict, dict]:
    earn_body,   earn_url   = _get("/lend/v1/earn/tokens")
    try:
        borrow_body, borrow_url = _get("/lend/v1/borrow/vaults")
    except Exception:
        borrow_body, borrow_url = [], earn_url  # borrow endpoint not yet live; use earn-only
    earn_list   = earn_body   if isinstance(earn_body,   list) else earn_body.get("tokens",  earn_body.get("data", []))
    borrow_list = borrow_body if isinstance(borrow_body, list) else borrow_body.get("vaults", borrow_body.get("data", []))
    result, _ = _parse_earn_borrow(earn_list, borrow_list, "v1/earn+borrow")
    debug = {
        "endpoint_used":     "v1/earn+borrow",
        "url":               f"{earn_url}  +  {borrow_url}",
        "stablecoins_found": ", ".join(sorted(result.keys())),
        "status":            "ok",
    }
    return result, debug


def _from_earn_and_borrow_v2() -> tuple[dict, dict]:
    earn_body,   earn_url   = _get("/lend/v2/earn/tokens")
    borrow_body, borrow_url = _get("/lend/v2/borrow/vaults")
    earn_list   = earn_body   if isinstance(earn_body,   list) else earn_body.get("tokens",  earn_body.get("data", []))
    borrow_list = borrow_body if isinstance(borrow_body, list) else borrow_body.get("vaults", borrow_body.get("data", []))
    result, _ = _parse_earn_borrow(earn_list, borrow_list, "v2/earn+borrow")
    debug = {
        "endpoint_used":     "v2/earn+borrow",
        "url":               f"{earn_url}  +  {borrow_url}",
        "stablecoins_found": ", ".join(sorted(result.keys())),
        "status":            "ok",
    }
    return result, debug


def _from_markets() -> tuple[dict, dict]:
    body, url = _get("/lend/v1/markets")
    markets   = body if isinstance(body, list) else body.get("markets", body.get("data", []))

    result: dict[str, dict] = {}
    for m in markets:
        sym = (
            m.get("symbol") or m.get("tokenSymbol")
            or m.get("token", {}).get("symbol") or ""
        ).upper().strip()
        if sym not in STABLECOINS:
            continue
        supply = _to_pct(
            m.get("supplyAPY") or m.get("depositAPY") or m.get("supplyInterestAPY")
            or m.get("supplyApy") or m.get("lendApy")
        )
        borrow = _to_pct(
            m.get("borrowAPY") or m.get("borrowInterestAPY")
            or m.get("borrowApy") or m.get("borrowRate")
        )
        util = float(m.get("utilization") or m.get("utilizationRate") or 0)
        if util > 1:
            util /= 100
        if supply is not None and borrow is not None:
            result[sym] = {
                "supply_apy":    supply,
                "borrow_apy":    borrow,
                "utilization":   round(util, 4),
                "ltv":           LTV_PARAMS["JupLend"]["ltv"],
                "liq_threshold": LTV_PARAMS["JupLend"]["liq"],
            }

    if not result:
        sample_keys = list({k for m in markets[:3] for k in m})
        raise ValueError(f"JupLend /lend/v1/markets: no stablecoin data. keys={sample_keys}")

    debug = {
        "endpoint_used":     "v1/markets",
        "url":               url,
        "stablecoins_found": ", ".join(sorted(result.keys())),
        "status":            "ok",
    }
    return result, debug


def _from_tokens() -> tuple[dict, dict]:
    body, url = _get("/lend/v1/tokens")
    tokens    = body if isinstance(body, list) else body.get("tokens", body.get("data", []))

    result: dict[str, dict] = {}
    for t in tokens:
        sym = (t.get("symbol") or t.get("tokenSymbol") or "").upper().strip()
        if sym not in STABLECOINS:
            continue
        supply = _to_pct(
            t.get("supplyAPY") or t.get("depositAPY") or t.get("lendingAPY")
            or t.get("supplyApy") or t.get("lendApy")
        )
        borrow = _to_pct(
            t.get("borrowAPY") or t.get("borrowingAPY")
            or t.get("borrowApy") or t.get("borrowRate")
        )
        util = float(t.get("utilization") or t.get("utilizationRate") or 0)
        if util > 1:
            util /= 100
        if supply is not None and borrow is not None:
            result[sym] = {
                "supply_apy":    supply,
                "borrow_apy":    borrow,
                "utilization":   round(util, 4),
                "ltv":           LTV_PARAMS["JupLend"]["ltv"],
                "liq_threshold": LTV_PARAMS["JupLend"]["liq"],
            }

    if not result:
        sample_keys = list({k for t in tokens[:3] for k in t})
        raise ValueError(f"JupLend /lend/v1/tokens: no stablecoin data. keys={sample_keys}")

    debug = {
        "endpoint_used":     "v1/tokens",
        "url":               url,
        "stablecoins_found": ", ".join(sorted(result.keys())),
        "status":            "ok",
    }
    return result, debug
