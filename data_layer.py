"""
data_layer.py — Aggregation, calculations, derived metrics, and logging.

Logging format (append to lending.log):
    timestamp | level | app | metric_tag | info
"""

from __future__ import annotations

import os
import time
from datetime import datetime

import numpy as np
import streamlit as st

from api import (
    STABLECOINS,
    PROTOCOLS,
    fetch_kamino_rates,
    fetch_juplend_rates,
    fetch_drift_rates,
)

# ─── LOGGER ───────────────────────────────────────────────────────────────────

_LOG_DIR = os.path.dirname(__file__)
_APP     = "solana-lending-dashboard"


def _log(level: str, tag: str, info: str) -> None:
    """
    Append one line to lending-YYYY-MM-DD.log.
    Filename evaluated on every call so it rolls over at midnight automatically.

    Format:
        2026-02-25 14:32:01 | INFO  | solana-lending-dashboard | kamino.fetch | ok
    """
    now      = datetime.now()
    log_file = os.path.join(_LOG_DIR, f"lending-{now.strftime('%Y-%m-%d')}.log")
    ts       = now.strftime("%Y-%m-%d %H:%M:%S")
    line     = f"{ts} | {level:<5} | {_APP} | {tag:<30} | {info}\n"
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass  # never let logging crash the app


# ─── STALE DATA CACHE ─────────────────────────────────────────────────────────
# When a live fetch fails, we return the last successful result (if ≤5 min old)
# so the UI degrades gracefully instead of losing all data immediately.

_STALE_TTL = 300  # seconds (5 minutes)

# dict[protocol] = (rates_dict, unix_timestamp_of_fetch)
_last_good: dict[str, tuple[dict, float]] = {}


# ─── PER-PROTOCOL CACHED FETCHERS ─────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def _cached_kamino() -> tuple[dict, str | None, dict[str, str]]:
    """Returns (rates, error|None, debug_dict)."""
    try:
        rates, debug = fetch_kamino_rates()
        _log("INFO",  "kamino.fetch", f"ok | {debug.get('stablecoins_found')} | {debug.get('url')}")
        return rates, None, debug
    except Exception as exc:
        _log("ERROR", "kamino.fetch", str(exc))
        return {}, str(exc), {"status": "error", "error": str(exc)}


@st.cache_data(ttl=60, show_spinner=False)
def _cached_juplend() -> tuple[dict, str | None, dict[str, str]]:
    try:
        rates, debug = fetch_juplend_rates()
        _log("INFO",  "juplend.fetch",
             f"ok | {debug.get('stablecoins_found')} | "
             f"endpoint={debug.get('endpoint_used')} | attempt={debug.get('attempt')}")
        if debug.get("attempts_log"):
            _log("INFO", "juplend.attempts", debug["attempts_log"])
        return rates, None, debug
    except Exception as exc:
        _log("ERROR", "juplend.fetch", str(exc))
        return {}, str(exc), {"status": "error", "error": str(exc)}


@st.cache_data(ttl=60, show_spinner=False)
def _cached_drift() -> tuple[dict, str | None, dict[str, str]]:
    try:
        rates, debug = fetch_drift_rates()
        _log("INFO",  "drift.fetch",
             f"ok | {debug.get('stablecoins_found')} | raw={debug.get('raw_sample', '')}")
        return rates, None, debug
    except Exception as exc:
        _log("ERROR", "drift.fetch", str(exc))
        return {}, str(exc), {"status": "error", "error": str(exc)}


_FETCHERS = {
    "Kamino":  _cached_kamino,
    "JupLend": _cached_juplend,
    "Drift":   _cached_drift,
}


# ─── PUBLIC FETCH FUNCTION ────────────────────────────────────────────────────

def fetch_all_rates() -> tuple[dict, str, dict[str, str], dict[str, dict[str, str]]]:
    """
    Fetch live rates from all protocols.

    Returns
    -------
    rates      dict[protocol][stable] -> rate_dict | None
    fetched_at str                    – HH:MM:SS timestamp
    errors     dict[protocol]         -> error message (empty if all ok)
                                         value is "[STALE] …" when serving cached data
    debug      dict[protocol]         -> debug key/value dict per protocol
    """
    fetched_at = datetime.now().strftime("%H:%M:%S")
    errors: dict[str, str]            = {}
    rates:  dict[str, dict]           = {}
    debug:  dict[str, dict[str, str]] = {}

    for protocol in PROTOCOLS:
        raw, err, dbg = _FETCHERS[protocol]()

        if err:
            # Attempt stale fallback
            cached = _last_good.get(protocol)
            now    = time.time()
            if cached is not None and (now - cached[1]) <= _STALE_TTL:
                age_s  = int(now - cached[1])
                age_m  = age_s // 60
                age_label = f"{age_m}m {age_s % 60}s" if age_m else f"{age_s}s"
                errors[protocol] = f"[STALE:{age_label}] {err}"
                raw  = cached[0]
                dbg  = {**dbg, "status": f"stale ({age_label} old)", "stale_error": err[:120]}
                _log("WARN", "fetch_all_rates",
                     f"{protocol} STALE fallback ({age_label} old) | err={err[:80]}")
            else:
                errors[protocol] = err
                _log("WARN", "fetch_all_rates", f"{protocol} failed: {err[:120]}")
        else:
            # Fresh data — update the last-good cache
            _last_good[protocol] = (raw, time.time())

        rates[protocol] = {stable: raw.get(stable) for stable in STABLECOINS}
        debug[protocol] = dbg

    _log("INFO", "fetch_all_rates", f"cycle complete | errors={list(errors.keys()) or 'none'}")
    return rates, fetched_at, errors, debug


# ─── SUMMARY METRICS ──────────────────────────────────────────────────────────

def compute_summary(rates: dict) -> dict:
    best_rate  = float("inf")
    best_proto = "—"
    best_asset = "—"
    all_borrows: list[float] = []

    for proto, stables in rates.items():
        for stable, data in stables.items():
            if data:
                b = data["borrow_apy"]
                all_borrows.append(b)
                if b < best_rate:
                    best_rate  = b
                    best_proto = proto
                    best_asset = stable

    avg_borrow  = round(float(np.mean(all_borrows)), 2) if all_borrows else 0.0
    usdc_rates  = [rates[p]["USDC"]["borrow_apy"] for p in PROTOCOLS if rates[p].get("USDC")]
    usdc_spread = round(max(usdc_rates) - min(usdc_rates), 2) if len(usdc_rates) > 1 else 0.0
    all_ltvs    = [rates[p]["USDC"]["ltv"] for p in PROTOCOLS if rates[p].get("USDC")]
    dfdv_ltv    = max(all_ltvs) if all_ltvs else 0

    return {
        "best_borrow_rate":  round(best_rate, 2) if best_rate != float("inf") else 0.0,
        "best_borrow_proto": best_proto,
        "best_borrow_asset": best_asset,
        "avg_borrow":        avg_borrow,
        "usdc_spread":       usdc_spread,
        "dfdv_ltv":          dfdv_ltv,
    }


# ─── CROSS-PROTOCOL ARB PAIRS ─────────────────────────────────────────────────

def compute_arb_pairs(rates: dict) -> list[dict]:
    rows: list[dict] = []
    for stable in STABLECOINS:
        pairs: list[dict] = []
        for b_proto in PROTOCOLS:
            bd = rates[b_proto].get(stable)
            if not bd:
                continue
            for s_proto in PROTOCOLS:
                sd = rates[s_proto].get(stable)
                if not sd:
                    continue
                pairs.append({
                    "stable":       stable,
                    "borrow_proto": b_proto,
                    "borrow_apy":   bd["borrow_apy"],
                    "supply_proto": s_proto,
                    "supply_apy":   sd["supply_apy"],
                    "net":          round(sd["supply_apy"] - bd["borrow_apy"], 2),
                })
        if not pairs:
            continue
        pairs.sort(key=lambda x: x["net"], reverse=True)
        for i, p in enumerate(pairs[:2]):
            rows.append({**p, "rank": i + 1})
    return rows


# ─── UTILIZATION CURVE ────────────────────────────────────────────────────────

def compute_utilization_curve(
    borrow_apy_at_current: float,
    current_util: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    u             = np.linspace(0.001, 1.0, 300)
    kink1, kink2  = 0.80, 0.90
    base          = 0.5
    safe_util     = max(current_util, 0.01)
    slope_implied = max((borrow_apy_at_current - base) / safe_util, 0.1)
    slope_normal  = slope_implied * 0.7
    slope_kink1   = slope_implied * 2.5
    slope_kink2   = slope_implied * 10.0

    borrow = np.where(
        u <= kink1,
        base + slope_normal * u,
        np.where(
            u <= kink2,
            base + slope_normal * kink1 + slope_kink1 * (u - kink1),
            base + slope_normal * kink1
                 + slope_kink1 * (kink2 - kink1)
                 + slope_kink2 * (u - kink2),
        ),
    )
    borrow = np.clip(borrow, base, borrow_apy_at_current * 6)
    supply = borrow * u * 0.85
    return u * 100, borrow, supply


# ─── NET YIELD CALCULATOR ─────────────────────────────────────────────────────

def compute_net_yield(
    dfdv_apy:     float,
    ltv_used_pct: float,
    borrow_apy:   float,
    supply_apy:   float,
) -> dict:
    ltv        = ltv_used_pct / 100
    earned     = round(supply_apy * ltv, 2)
    paid       = round(borrow_apy * ltv, 2)
    net        = round(dfdv_apy + earned - paid, 2)
    vs_hold    = round(net - dfdv_apy, 2)
    arb_spread = round(supply_apy - borrow_apy, 2)
    return {
        "staking":    dfdv_apy,
        "earned":     earned,
        "paid":       paid,
        "net":        net,
        "vs_hold":    vs_hold,
        "arb_spread": arb_spread,
    }
