"""Shared HTTP handling for Graph API calls (error classification)."""

from __future__ import annotations

from typing import Any

import httpx

from app.integrations.errors import ExternalAPIError

# Graph error codes that mean "try again later" (rate limits / temporary issues).
_TRANSIENT_CODES = {1, 2, 4, 17, 32, 341, 613, 80007, 130429, 131016}


def check_response(resp: httpx.Response, what: str) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code == 200 and "error" not in data:
        return data
    err = data.get("error") or {}
    code = err.get("code")
    transient = resp.status_code >= 500 or resp.status_code == 429 or code in _TRANSIENT_CODES
    message = err.get("message") or resp.text[:300]
    raise ExternalAPIError(
        f"{what} failed ({resp.status_code}, code {code}): {message}", transient=transient, code=code
    )


def request(client: httpx.Client, method: str, url: str, what: str, **kwargs: Any) -> dict[str, Any]:
    try:
        resp = client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise ExternalAPIError(f"{what} failed: {exc}", transient=True) from exc
    return check_response(resp, what)
