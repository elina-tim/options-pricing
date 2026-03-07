"""
tests/test_drift.py — Integration tests for api/test_drift.py.

Each test:
  1. Runs the live query (fetch_drift_rates — syncs over asyncio.run internally)
  2. Parses / validates the response structure
  3. Checks every returned coin's values are within sensible ranges

Note: Drift currently only lists USDC (idx 0), PYUSD (idx 6), and USDS (idx 8)
on mainnet-beta.  Other STABLECOINS are skipped automatically.

Run with:
    pytest tests/test_drift.py -v
"""

import pytest
from api import fetch_drift_rates, STABLECOINS, LTV_PARAMS

# ── Expected LTV constants from constants.py ──────────────────────────────────
_EXPECTED_LTV = LTV_PARAMS["Drift"]["ltv"]
_EXPECTED_LIQ = LTV_PARAMS["Drift"]["liq"]

# ── Coins that Drift mainnet-beta currently lists ─────────────────────────────
_DRIFT_LISTED = {"USDC", "PYUSD", "USDS"}

# ── Reasonable market-rate bounds (percent) ───────────────────────────────────
_MAX_SUPPLY_APY = 50.0
_MAX_BORROW_APY = 100.0


# ── Shared fixture (query is executed exactly once per test session) ──────────

@pytest.fixture(scope="module")
def drift_data() -> tuple[dict, dict]:
    """Call the live Drift on-chain reader and return (rates, debug)."""
    rates, debug = fetch_drift_rates()
    return rates, debug


# ── Top-level structural tests ────────────────────────────────────────────────

class TestDriftResponse:
    def test_returns_nonempty_dict(self, drift_data):
        rates, _ = drift_data
        assert isinstance(rates, dict), "rates must be a dict"
        assert len(rates) > 0, "rates dict must not be empty"

    def test_debug_has_required_keys(self, drift_data):
        _, debug = drift_data
        for key in ("rpc", "source", "markets_fetched", "stablecoins_found", "status"):
            assert key in debug, f"debug missing key '{key}'"

    def test_debug_status_ok(self, drift_data):
        _, debug = drift_data
        assert debug["status"] == "ok", f"unexpected status: {debug['status']}"

    def test_all_symbols_are_known_stablecoins(self, drift_data):
        rates, _ = drift_data
        for symbol in rates:
            assert symbol in STABLECOINS, f"unexpected symbol in response: {symbol}"

    def test_only_listed_symbols_returned(self, drift_data):
        rates, _ = drift_data
        for symbol in rates:
            assert symbol in _DRIFT_LISTED, (
                f"{symbol} returned but not in Drift's listed markets {_DRIFT_LISTED}"
            )


# ── Per-coin parametrised tests ───────────────────────────────────────────────

@pytest.mark.parametrize("symbol", sorted(_DRIFT_LISTED))
class TestDriftCoinValues:
    """For every stablecoin that Drift returns, validate all fields."""

    def _get(self, drift_data, symbol):
        rates, _ = drift_data
        if symbol not in rates:
            pytest.skip(f"{symbol} not returned by Drift — skipping")
        return rates[symbol]

    # ── structure ─────────────────────────────────────────────────────────────

    def test_has_required_keys(self, drift_data, symbol):
        coin = self._get(drift_data, symbol)
        for key in ("supply_apy", "borrow_apy", "utilization", "ltv", "liq_threshold"):
            assert key in coin, f"{symbol}: missing key '{key}'"

    def test_all_values_are_numeric(self, drift_data, symbol):
        coin = self._get(drift_data, symbol)
        for key, val in coin.items():
            assert isinstance(val, (int, float)), (
                f"{symbol}: '{key}' is {type(val).__name__}, expected numeric"
            )

    # ── supply_apy ────────────────────────────────────────────────────────────

    def test_supply_apy_is_non_negative(self, drift_data, symbol):
        coin = self._get(drift_data, symbol)
        assert coin["supply_apy"] >= 0.0, (
            f"{symbol}: supply_apy {coin['supply_apy']} < 0"
        )

    def test_supply_apy_below_ceiling(self, drift_data, symbol):
        coin = self._get(drift_data, symbol)
        assert coin["supply_apy"] <= _MAX_SUPPLY_APY, (
            f"{symbol}: supply_apy {coin['supply_apy']} exceeds {_MAX_SUPPLY_APY}%"
        )

    # ── borrow_apy ────────────────────────────────────────────────────────────

    def test_borrow_apy_is_non_negative(self, drift_data, symbol):
        coin = self._get(drift_data, symbol)
        assert coin["borrow_apy"] >= 0.0, (
            f"{symbol}: borrow_apy {coin['borrow_apy']} < 0"
        )

    def test_borrow_apy_below_ceiling(self, drift_data, symbol):
        coin = self._get(drift_data, symbol)
        assert coin["borrow_apy"] <= _MAX_BORROW_APY, (
            f"{symbol}: borrow_apy {coin['borrow_apy']} exceeds {_MAX_BORROW_APY}%"
        )

    def test_borrow_apy_gte_supply_apy(self, drift_data, symbol):
        coin = self._get(drift_data, symbol)
        assert coin["borrow_apy"] >= coin["supply_apy"], (
            f"{symbol}: borrow_apy {coin['borrow_apy']} < supply_apy {coin['supply_apy']}"
        )

    # ── utilization ───────────────────────────────────────────────────────────

    def test_utilization_in_unit_interval(self, drift_data, symbol):
        coin = self._get(drift_data, symbol)
        u = coin["utilization"]
        assert 0.0 <= u <= 1.0, f"{symbol}: utilization {u} outside [0, 1]"

    # ── ltv / liq_threshold ───────────────────────────────────────────────────

    def test_ltv_matches_constant(self, drift_data, symbol):
        coin = self._get(drift_data, symbol)
        assert coin["ltv"] == _EXPECTED_LTV, (
            f"{symbol}: ltv {coin['ltv']} != {_EXPECTED_LTV}"
        )

    def test_liq_threshold_matches_constant(self, drift_data, symbol):
        coin = self._get(drift_data, symbol)
        assert coin["liq_threshold"] == _EXPECTED_LIQ, (
            f"{symbol}: liq_threshold {coin['liq_threshold']} != {_EXPECTED_LIQ}"
        )

    def test_liq_threshold_gt_ltv(self, drift_data, symbol):
        coin = self._get(drift_data, symbol)
        assert coin["liq_threshold"] > coin["ltv"], (
            f"{symbol}: liq_threshold {coin['liq_threshold']} <= ltv {coin['ltv']}"
        )
