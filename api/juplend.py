"""
api/juplend.py — Jupiter Lend live rate fetcher.

Tries three endpoints in order:
  1. /v1/earn/tokens  +  /v1/borrow/vaults   (current split API)
  2. /v1/markets                              (combined legacy)
  3. /v1/tokens                               (alternate)
"""

import requests
from .constants import STABLECOINS, LTV_PARAMS

_BASE    = "https://lend-api.jup.ag"
_TIMEOUT = 12


def _to_pct(v) -> float | None:
    if v is None:
        return None
    v = float(v)
    return round(v * 100 if abs(v) < 1 else v, 3)


def _get(path: str) -> tuple[list | dict, str]:
    url  = f"{_BASE}{path}"
    resp = requests.get(url, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json(), url


def fetch_juplend_rates() -> tuple[dict[str, dict], dict[str, str]]:
    """
    Returns
    -------
    rates   dict[symbol] -> rate_dict
    debug   dict[str, str] of diagnostic key/values:
              endpoint_used, url, stablecoins_found, status
    """
    last_err: Exception | None = None

    for attempt, fn in enumerate([
        _from_earn_and_borrow,
        _from_markets,
        _from_tokens,
    ], start=1):
        try:
            result, debug = fn()
            debug["attempt"] = str(attempt)
            return result, debug
        except Exception as e:
            last_err = e

    raise last_err or ValueError("JupLend: all endpoints failed.")


def _from_earn_and_borrow() -> tuple[dict, dict]:
    earn_body,   earn_url   = _get("/v1/earn/tokens")
    borrow_body, borrow_url = _get("/v1/borrow/vaults")

    earn_list   = earn_body   if isinstance(earn_body,   list) else earn_body.get("tokens",  earn_body.get("data", []))
    borrow_list = borrow_body if isinstance(borrow_body, list) else borrow_body.get("vaults", borrow_body.get("data", []))

    supply_map: dict[str, float] = {}
    for t in earn_list:
        sym = (t.get("symbol") or t.get("tokenSymbol") or "").upper().strip()
        if sym not in STABLECOINS:
            continue
        apy = _to_pct(t.get("supplyAPY") or t.get("depositAPY") or t.get("apy"))
        if apy is not None:
            supply_map[sym] = apy

    borrow_map: dict[str, dict] = {}
    for v in borrow_list:
        sym = (v.get("symbol") or v.get("tokenSymbol") or
               v.get("debtToken", {}).get("symbol") or "").upper().strip()
        if sym not in STABLECOINS:
            continue
        apy  = _to_pct(v.get("borrowAPY") or v.get("borrowInterestAPY") or v.get("rate"))
        util = float(v.get("utilization") or v.get("utilizationRate") or 0)
        if util > 1:
            util /= 100
        if apy is not None:
            borrow_map[sym] = {"borrow_apy": apy, "utilization": round(util, 4)}

    result: dict[str, dict] = {}
    for sym in STABLECOINS:
        if sym in supply_map and sym in borrow_map:
            result[sym] = {
                "supply_apy":    supply_map[sym],
                "borrow_apy":    borrow_map[sym]["borrow_apy"],
                "utilization":   borrow_map[sym]["utilization"],
                "ltv":           LTV_PARAMS["JupLend"]["ltv"],
                "liq_threshold": LTV_PARAMS["JupLend"]["liq"],
            }

    if not result:
        raise ValueError("JupLend earn+borrow: no matching stablecoin data.")

    debug = {
        "endpoint_used":     "earn+borrow",
        "url":               f"{earn_url}  +  {borrow_url}",
        "stablecoins_found": ", ".join(sorted(result.keys())),
        "status":            "ok",
    }
    return result, debug


def _from_markets() -> tuple[dict, dict]:
    body, url = _get("/v1/markets")
    markets   = body if isinstance(body, list) else body.get("markets", body.get("data", []))

    result: dict[str, dict] = {}
    for m in markets:
        sym = (m.get("symbol") or m.get("tokenSymbol") or
               m.get("token", {}).get("symbol") or "").upper().strip()
        if sym not in STABLECOINS:
            continue
        supply = _to_pct(m.get("supplyAPY") or m.get("depositAPY") or m.get("supplyInterestAPY"))
        borrow = _to_pct(m.get("borrowAPY") or m.get("borrowInterestAPY"))
        util   = float(m.get("utilization") or m.get("utilizationRate") or 0)
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
        raise ValueError("JupLend /v1/markets: no stablecoin data.")

    debug = {
        "endpoint_used":     "markets",
        "url":               url,
        "stablecoins_found": ", ".join(sorted(result.keys())),
        "status":            "ok",
    }
    return result, debug


def _from_tokens() -> tuple[dict, dict]:
    body, url = _get("/v1/tokens")
    tokens    = body if isinstance(body, list) else body.get("tokens", body.get("data", []))

    result: dict[str, dict] = {}
    for t in tokens:
        sym = (t.get("symbol") or t.get("tokenSymbol") or "").upper().strip()
        if sym not in STABLECOINS:
            continue
        supply = _to_pct(t.get("supplyAPY") or t.get("depositAPY") or t.get("lendingAPY"))
        borrow = _to_pct(t.get("borrowAPY") or t.get("borrowingAPY"))
        util   = float(t.get("utilization") or t.get("utilizationRate") or 0)
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
        raise ValueError("JupLend /v1/tokens: no stablecoin data.")

    debug = {
        "endpoint_used":     "tokens",
        "url":               url,
        "stablecoins_found": ", ".join(sorted(result.keys())),
        "status":            "ok",
    }
    return result, debug