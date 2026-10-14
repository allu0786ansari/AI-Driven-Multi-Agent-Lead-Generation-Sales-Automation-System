"""Meta webhook signature verification (X-Hub-Signature-256).

Meta signs the raw request body with the app secret using HMAC-SHA256 and sends
`X-Hub-Signature-256: sha256=<hex digest>`. Any request without a valid
signature is rejected, so nobody can post fake leads to our endpoints.
"""

from __future__ import annotations

import hashlib
import hmac

SIGNATURE_HEADER = "X-Hub-Signature-256"
_PREFIX = "sha256="


def compute_signature(body: bytes, app_secret: str) -> str:
    digest = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return _PREFIX + digest


def is_valid_signature(body: bytes, header_value: str | None, app_secret: str) -> bool:
    if not header_value or not header_value.startswith(_PREFIX):
        return False
    expected = compute_signature(body, app_secret)
    return hmac.compare_digest(expected, header_value.strip())
