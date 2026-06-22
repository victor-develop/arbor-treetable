"""Webhook payload signing (ARCHITECTURE §7).

Pure stdlib HMAC-SHA256. Each webhook delivery carries
``X-Arbor-Signature: sha256=<hex>`` where the digest is over the raw request
body keyed by the endpoint secret. Deterministic and constant-time-comparable.
"""

from __future__ import annotations

import hashlib
import hmac


def compute_signature(secret: str, body: bytes | str) -> str:
    """Return ``sha256=<hexdigest>`` — the value of ``X-Arbor-Signature``."""
    if isinstance(body, str):
        body = body.encode("utf-8")
    if isinstance(secret, str):
        secret_bytes = secret.encode("utf-8")
    else:  # pragma: no cover - defensive
        secret_bytes = secret
    digest = hmac.new(secret_bytes, body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, body: bytes | str, signature: str) -> bool:
    """Constant-time verification of an ``X-Arbor-Signature`` header value."""
    expected = compute_signature(secret, body)
    return hmac.compare_digest(expected, signature or "")
