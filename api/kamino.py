"""
api/kamino.py — Kamino Finance live rate fetcher.

Market discovery: GET /v2/kamino-market  (cached 24h)
Rate fetch:       GET /kamino-market/{pubkey}/reserves/metrics

Response fields per reserve:
    liquidityToken  str   e.g. "USDC"
    borrowApy       str   decimal  e.g. "0.04549" = 4.549%
    supplyApy       str   decimal  e.g. "0.02556" = 2.556%
    maxLtv          str   decimal  e.g. "0.8"     = 80%
    totalBorrow     str   token units
    totalSupply     str   token units
"""

import time
from ._http import get_json
from .constants import STABLECOINS, LTV_PARAMS

_BASE       = "https://api.kamino.finance"
_TIMEOUT    = 60
_MARKET_TTL = 86_400  # 24 hours

_market_cache: tuple[str, float] | None = None  # (pubkey, fetched_at_unix)


def _get(path: str) -> tuple[list | dict, str]:
    return get_json(path, base=_BASE, timeout=_TIMEOUT, retries=2, backoff=2.0)


def _fetch_main_market_pubkey() -> str:
    markets, _ = _get("/v2/kamino-market")
    if not isinstance(markets, list):
        markets = markets.get("markets") or markets.get("data") or []
    if not markets:
        raise ValueError("Kamino /v2/kamino-market returned an empty list.")

    best_pubkey = None
    best_count  = -1
    for m in markets:
        pubkey = (
            m.get("lendingMarket") or m.get("marketAddress")
            or m.get("pubkey")     or m.get("address") or ""
        )
        if not pubkey:
            continue
        reserves = m.get("reserves") or []
        count = sum(
            1 for r in reserves
            if (r.get("liquidityToken") or r.get("symbol") or "").upper() in STABLECOINS
        )
        if count > best_count:
            best_count  = count
            best_pubkey = pubkey

    if not best_pubkey:
        raise ValueError("Kamino: could not extract a market pubkey from /v2/kamino-market.")
    return best_pubkey


def _get_market_pubkey() -> str:
    global _market_cache
    now = time.time()
    if _market_cache is not None:
        pubkey, fetched_at = _market_cache
        if now - fetched_at < _MARKET_TTL:
            return pubkey
    pubkey = _fetch_main_market_pubkey()
    _market_cache = (pubkey, now)
    return pubkey


def fetch_kamino_rates() -> tuple[dict[str, dict], dict[str, str]]:
    """
    Returns
    -------
    rates   dict[symbol] -> rate_dict
    debug   dict[str, str] of diagnostic key/values:
              url, market_pubkey, records_total, stablecoins_found, status
    """
    market = _get_market_pubkey()
    records, url = _get(f"/kamino-market/{market}/reserves/metrics")
    if not isinstance(records, list):
        records = records.get("reserves") or records.get("data") or []

    result: dict[str, dict] = {}
    for r in records:
        symbol = (r.get("liquidityToken") or "").upper().strip()
        if symbol not in STABLECOINS:
            continue

        supply_apy   = round(float(r.get("supplyApy") or 0) * 100, 3)
        borrow_apy   = round(float(r.get("borrowApy") or 0) * 100, 3)
        total_supply = float(r.get("totalSupply") or 0)
        total_borrow = float(r.get("totalBorrow") or 0)
        utilization  = round(total_borrow / total_supply, 4) if total_supply > 0 else 0.0
        max_ltv_raw  = r.get("maxLtv")
        ltv          = round(float(max_ltv_raw) * 100) if max_ltv_raw else LTV_PARAMS["Kamino"]["ltv"]

        result[symbol] = {
            "supply_apy":    supply_apy,
            "borrow_apy":    borrow_apy,
            "utilization":   min(utilization, 1.0),
            "ltv":           ltv,
            "liq_threshold": LTV_PARAMS["Kamino"]["liq"],
        }

    debug = {
        "url":               url,
        "market_pubkey":     market,
        "records_total":     str(len(records)),
        "stablecoins_found": ", ".join(sorted(result.keys())) or "none",
        "status":            "ok" if result else "no_stablecoin_data",
    }

    if not result:
        raise ValueError(
            f"Kamino: no stablecoin reserves found. "
            f"Available: {[r.get('liquidityToken') for r in records[:10]]}"
        )

    return result, debug