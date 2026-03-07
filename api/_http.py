"""
api/_http.py — Shared HTTP helper with retry and diagnostic logging.

Usage
-----
    from api._http import get_json
    data, url = get_json("/v1/markets", base="https://lend-api.jup.ag", timeout=20)

Retry policy
------------
- Retries on connection errors and timeouts only (not on 4xx / 5xx responses)
- Exponential back-off: wait = backoff * (attempt - 1)  seconds
- On every failure logs: attempt #, error type, HTTP status (if available),
  and the first 300 chars of the response body (if available)
"""

from __future__ import annotations

import time
import requests


def get_json(
    path: str,
    *,
    base: str,
    timeout: int = 15,
    retries: int = 2,
    backoff: float = 1.0,
) -> tuple[list | dict, str]:
    """
    GET ``base + path`` and return ``(parsed_json, full_url)``.

    Parameters
    ----------
    path    : URL path (e.g. "/v1/markets")
    base    : Base URL without trailing slash
    timeout : Per-attempt socket timeout in seconds
    retries : Extra attempts after the first (total attempts = retries + 1)
    backoff : Seconds to wait before retry attempt N  (N * backoff)

    Raises
    ------
    requests.HTTPError      on non-2xx responses (after all retries exhausted)
    requests.ConnectionError / Timeout on persistent network failure
    ValueError              if response body is not valid JSON
    """
    url          = f"{base}{path}"
    last_exc: Exception | None = None

    for attempt in range(1, retries + 2):  # 1-indexed, total = retries+1
        try:
            resp = requests.get(url, timeout=timeout)

            # Log non-2xx before raising so callers can see the body in logs
            if not resp.ok:
                snippet = resp.text[:300].replace("\n", " ")
                raise requests.HTTPError(
                    f"HTTP {resp.status_code} from {url!r} — {snippet}",
                    response=resp,
                )

            return resp.json(), url

        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt <= retries:
                time.sleep(backoff * attempt)
            # Let loop continue for next retry

        except requests.HTTPError:
            # Don't retry on HTTP errors (4xx / 5xx) — re-raise immediately
            raise

    # All retries exhausted on network-level errors
    raise last_exc  # type: ignore[misc]
