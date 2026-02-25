"""
api/__init__.py — Public interface for the API layer.

Import everything you need from here:
    from api import STABLECOINS, PROTOCOLS, fetch_kamino_rates, ...
"""

from .constants import STABLECOINS, LTV_PARAMS, PROTOCOLS
from .kamino    import fetch_kamino_rates
from .juplend   import fetch_juplend_rates
from .drift     import fetch_drift_rates

__all__ = [
    "STABLECOINS",
    "LTV_PARAMS",
    "PROTOCOLS",
    "fetch_kamino_rates",
    "fetch_juplend_rates",
    "fetch_drift_rates",
]