"""
tests/test_juplend.py — Integration tests for api/juplend.py.

Each test:
  1. Runs the live query (fetch_juplend_rates)
  2. Parses / validates the response structure
  3. Checks every returned coin's values are within sensible ranges

Run with:
    pytest tests/test_juplend.py -v
"""

import pytest
from api import fetch_juplend_rates, STABLECOINS, LTV_PARAMS

# ── Expected LTV constants from constants.py ──────────────────────────────────
_EXPECTED_LTV = LTV_PARAMS["JupLend"]["ltv"]
_EXPECTED_LIQ = LTV_PARAMS["JupLend"]["liq"]

# ── Reasonable market-rate bounds (percent) ───────────────────────────────────
_MAX_SUPPLY_APY = 50.0
_MAX_BORROW_APY = 100.0


# ── Shared fixture (query is executed exactly once per test session) ──────────

@pytest.fixture(scope="module")
def juplend_data() -> tuple[dict, dict]:
    """Call the live JupLend API and return (rates, debug)."""
    rates, debug = fetch_juplend_rates()
    return rates, debug


# ── Top-level structural tests ────────────────────────────────────────────────

class TestJupLendResponse:
    def test_returns_nonempty_dict(self, juplend_data):
        rates, _ = juplend_data
        assert isinstance(rates, dict), "rates must be a dict"
        assert len(rates) > 0, "rates dict must not be empty"

    def test_debug_has_required_keys(self, juplend_data):
        _, debug = juplend_data
        for key in ("endpoint_used", "url", "stablecoins_found", "status"):
            assert key in debug, f"debug missing key '{key}'"

    def test_debug_status_ok(self, juplend_data):
        _, debug = juplend_data
        assert debug["status"] == "ok", f"unexpected status: {debug['status']}"

    def test_all_symbols_are_known_stablecoins(self, juplend_data):
        rates, _ = juplend_data
        for symbol in rates:
            assert symbol in STABLECOINS, f"unexpected symbol in response: {symbol}"

    def test_endpoint_used_is_valid(self, juplend_data):
        _, debug = juplend_data
        assert debug["endpoint_used"] in (
            "v1/earn+borrow", "v2/earn+borrow", "v1/markets", "v1/tokens"
        ), f"unexpected endpoint_used: {debug['endpoint_used']}"


# ── Per-coin parametrised tests ───────────────────────────────────────────────

@pytest.mark.parametrize("symbol", STABLECOINS)
class TestJupLendCoinValues:
    """For every stablecoin that JupLend returns, validate all fields."""

    def _get(self, juplend_data, symbol):
        rates, _ = juplend_data
        if symbol not in rates:
            pytest.skip(f"{symbol} not listed on JupLend — skipping")
        return rates[symbol]

    # ── structure ─────────────────────────────────────────────────────────────

    def test_has_required_keys(self, juplend_data, symbol):
        coin = self._get(juplend_data, symbol)
        for key in ("supply_apy", "borrow_apy", "utilization", "ltv", "liq_threshold"):
            assert key in coin, f"{symbol}: missing key '{key}'"

    def test_all_values_are_numeric(self, juplend_data, symbol):
        coin = self._get(juplend_data, symbol)
        for key, val in coin.items():
            assert isinstance(val, (int, float)), (
                f"{symbol}: '{key}' is {type(val).__name__}, expected numeric"
            )

    # ── supply_apy ────────────────────────────────────────────────────────────

    def test_supply_apy_is_non_negative(self, juplend_data, symbol):
        coin = self._get(juplend_data, symbol)
        assert coin["supply_apy"] >= 0.0, (
            f"{symbol}: supply_apy {coin['supply_apy']} < 0"
        )

    def test_supply_apy_below_ceiling(self, juplend_data, symbol):
        coin = self._get(juplend_data, symbol)
        assert coin["supply_apy"] <= _MAX_SUPPLY_APY, (
            f"{symbol}: supply_apy {coin['supply_apy']} exceeds {_MAX_SUPPLY_APY}%"
        )

    # ── borrow_apy ────────────────────────────────────────────────────────────

    def test_borrow_apy_is_non_negative(self, juplend_data, symbol):
        coin = self._get(juplend_data, symbol)
        assert coin["borrow_apy"] >= 0.0, (
            f"{symbol}: borrow_apy {coin['borrow_apy']} < 0"
        )

    def test_borrow_apy_below_ceiling(self, juplend_data, symbol):
        coin = self._get(juplend_data, symbol)
        assert coin["borrow_apy"] <= _MAX_BORROW_APY, (
            f"{symbol}: borrow_apy {coin['borrow_apy']} exceeds {_MAX_BORROW_APY}%"
        )

    def test_borrow_apy_gte_supply_apy(self, juplend_data, symbol):
        coin = self._get(juplend_data, symbol)
        assert coin["borrow_apy"] >= coin["supply_apy"], (
            f"{symbol}: borrow_apy {coin['borrow_apy']} < supply_apy {coin['supply_apy']}"
        )

    # ── utilization ───────────────────────────────────────────────────────────

    def test_utilization_in_unit_interval(self, juplend_data, symbol):
        coin = self._get(juplend_data, symbol)
        u = coin["utilization"]
        assert 0.0 <= u <= 1.0, f"{symbol}: utilization {u} outside [0, 1]"

    # ── ltv / liq_threshold ───────────────────────────────────────────────────

    def test_ltv_matches_constant(self, juplend_data, symbol):
        coin = self._get(juplend_data, symbol)
        assert coin["ltv"] == _EXPECTED_LTV, (
            f"{symbol}: ltv {coin['ltv']} != {_EXPECTED_LTV}"
        )

    def test_liq_threshold_matches_constant(self, juplend_data, symbol):
        coin = self._get(juplend_data, symbol)
        assert coin["liq_threshold"] == _EXPECTED_LIQ, (
            f"{symbol}: liq_threshold {coin['liq_threshold']} != {_EXPECTED_LIQ}"
        )

    def test_liq_threshold_gt_ltv(self, juplend_data, symbol):
        coin = self._get(juplend_data, symbol)
        assert coin["liq_threshold"] > coin["ltv"], (
            f"{symbol}: liq_threshold {coin['liq_threshold']} <= ltv {coin['ltv']}"
        )
