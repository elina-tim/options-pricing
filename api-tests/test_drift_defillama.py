"""
tests/test_drift_defillama.py — Integration tests for api/drift_defillama.py.

The fetcher first tries DeFi Llama (which currently has no Drift lending data),
then falls back to a direct lightweight Solana JSON-RPC read.

Tests validate:
  1. Response structure (dict keys, debug fields)
  2. Per-coin values within sensible ranges
  3. borrow_apy can be None if a coin is not found (matching JupLend convention)

Run with:
    pytest api-tests/test_drift_defillama.py -v
"""

import pytest
from api import fetch_drift_defillama_rates, STABLECOINS, LTV_PARAMS

# ── Expected LTV constants ────────────────────────────────────────────────────
_EXPECTED_LTV = LTV_PARAMS["DriftDL"]["ltv"]
_EXPECTED_LIQ = LTV_PARAMS["DriftDL"]["liq"]

# ── Reasonable market-rate bounds (percent) ───────────────────────────────────
_MAX_SUPPLY_APY = 50.0
_MAX_BORROW_APY = 100.0

# ── Coins that Drift mainnet currently lists ──────────────────────────────────
# (USDG, PRIME not yet on Drift mainnet as of 2026-03)
_DRIFT_LISTED = {"USDC", "PYUSD", "USDS", "USD1", "CASH"}


# ── Shared fixture (query executed exactly once per test session) ─────────────

@pytest.fixture(scope="module")
def driftdl_data() -> tuple[dict, dict]:
    """Call fetch_drift_defillama_rates() and return (rates, debug)."""
    rates, debug = fetch_drift_defillama_rates()
    return rates, debug


# ── Top-level structural tests ────────────────────────────────────────────────

class TestDriftDLResponse:
    def test_returns_nonempty_dict(self, driftdl_data):
        rates, _ = driftdl_data
        assert isinstance(rates, dict), "rates must be a dict"
        assert len(rates) > 0, "rates dict must not be empty"

    def test_debug_has_required_keys(self, driftdl_data):
        _, debug = driftdl_data
        # source and status are always present regardless of DL vs RPC path
        for key in ("source", "status", "stablecoins_found"):
            assert key in debug, f"debug missing key '{key}'"

    def test_debug_status_ok(self, driftdl_data):
        _, debug = driftdl_data
        assert debug["status"] == "ok", f"unexpected status: {debug['status']}"

    def test_all_symbols_are_known_stablecoins(self, driftdl_data):
        rates, _ = driftdl_data
        for symbol in rates:
            assert symbol in STABLECOINS, f"unexpected symbol in response: {symbol}"

    def test_at_least_usdc_present(self, driftdl_data):
        rates, _ = driftdl_data
        assert "USDC" in rates, (
            f"USDC not found; got: {sorted(rates.keys())}"
        )

    def test_only_known_drift_symbols(self, driftdl_data):
        rates, _ = driftdl_data
        for symbol in rates:
            assert symbol in _DRIFT_LISTED, (
                f"{symbol} returned but not known to be listed on Drift mainnet "
                f"(known: {_DRIFT_LISTED})"
            )


# ── Per-coin parametrised tests ───────────────────────────────────────────────

@pytest.mark.parametrize("symbol", STABLECOINS)
class TestDriftDLCoinValues:
    """For every stablecoin that DriftDL returns, validate all fields."""

    def _get(self, driftdl_data, symbol):
        rates, _ = driftdl_data
        if symbol not in rates:
            pytest.skip(f"{symbol} not listed on DriftDL — skipping")
        return rates[symbol]

    # ── structure ─────────────────────────────────────────────────────────────

    def test_has_required_keys(self, driftdl_data, symbol):
        coin = self._get(driftdl_data, symbol)
        for key in ("supply_apy", "borrow_apy", "utilization", "ltv", "liq_threshold"):
            assert key in coin, f"{symbol}: missing key '{key}'"

    def test_non_borrow_values_are_numeric(self, driftdl_data, symbol):
        coin = self._get(driftdl_data, symbol)
        for key in ("supply_apy", "utilization", "ltv", "liq_threshold"):
            assert isinstance(coin[key], (int, float)), (
                f"{symbol}: '{key}' is {type(coin[key]).__name__}, expected numeric"
            )

    def test_borrow_apy_is_numeric_or_none(self, driftdl_data, symbol):
        coin = self._get(driftdl_data, symbol)
        assert coin["borrow_apy"] is None or isinstance(coin["borrow_apy"], (int, float)), (
            f"{symbol}: 'borrow_apy' is {type(coin['borrow_apy']).__name__}, expected numeric or None"
        )

    # ── supply_apy ────────────────────────────────────────────────────────────

    def test_supply_apy_is_non_negative(self, driftdl_data, symbol):
        coin = self._get(driftdl_data, symbol)
        assert coin["supply_apy"] >= 0.0, (
            f"{symbol}: supply_apy {coin['supply_apy']} < 0"
        )

    def test_supply_apy_below_ceiling(self, driftdl_data, symbol):
        coin = self._get(driftdl_data, symbol)
        assert coin["supply_apy"] <= _MAX_SUPPLY_APY, (
            f"{symbol}: supply_apy {coin['supply_apy']} exceeds {_MAX_SUPPLY_APY}%"
        )

    # ── borrow_apy ────────────────────────────────────────────────────────────

    def test_borrow_apy_non_negative_when_present(self, driftdl_data, symbol):
        coin = self._get(driftdl_data, symbol)
        if coin["borrow_apy"] is None:
            pytest.skip(f"{symbol}: borrow_apy=None — skipping")
        assert coin["borrow_apy"] >= 0.0, (
            f"{symbol}: borrow_apy {coin['borrow_apy']} < 0"
        )

    def test_borrow_apy_below_ceiling_when_present(self, driftdl_data, symbol):
        coin = self._get(driftdl_data, symbol)
        if coin["borrow_apy"] is None:
            pytest.skip(f"{symbol}: borrow_apy=None — skipping")
        assert coin["borrow_apy"] <= _MAX_BORROW_APY, (
            f"{symbol}: borrow_apy {coin['borrow_apy']} exceeds {_MAX_BORROW_APY}%"
        )

    def test_borrow_apy_gte_supply_apy_when_present(self, driftdl_data, symbol):
        coin = self._get(driftdl_data, symbol)
        if coin["borrow_apy"] is None:
            pytest.skip(f"{symbol}: borrow_apy=None — skipping")
        # Allow 0.001% tolerance for floating-point rounding
        assert coin["borrow_apy"] >= coin["supply_apy"] - 0.001, (
            f"{symbol}: borrow_apy {coin['borrow_apy']} < supply_apy {coin['supply_apy']}"
        )

    # ── utilization ───────────────────────────────────────────────────────────

    def test_utilization_in_unit_interval(self, driftdl_data, symbol):
        coin = self._get(driftdl_data, symbol)
        u = coin["utilization"]
        assert 0.0 <= u <= 1.0, f"{symbol}: utilization {u} outside [0, 1]"

    # ── ltv / liq_threshold ───────────────────────────────────────────────────

    def test_ltv_in_valid_range(self, driftdl_data, symbol):
        coin = self._get(driftdl_data, symbol)
        assert 1 <= coin["ltv"] <= 100, (
            f"{symbol}: ltv {coin['ltv']} outside [1, 100]"
        )

    def test_liq_threshold_matches_constant(self, driftdl_data, symbol):
        coin = self._get(driftdl_data, symbol)
        assert coin["liq_threshold"] == _EXPECTED_LIQ, (
            f"{symbol}: liq_threshold {coin['liq_threshold']} != {_EXPECTED_LIQ}"
        )

    def test_liq_threshold_gt_ltv(self, driftdl_data, symbol):
        coin = self._get(driftdl_data, symbol)
        assert coin["liq_threshold"] > coin["ltv"], (
            f"{symbol}: liq_threshold {coin['liq_threshold']} <= ltv {coin['ltv']}"
        )
