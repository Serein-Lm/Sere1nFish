"""Short-lived credential helpers for scan nodes and work leases."""

from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_secret(prefix: str, *, bytes_count: int = 32) -> str:
    return f"{prefix}_{secrets.token_urlsafe(max(24, bytes_count))}"


def secret_digest(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def stable_payload_digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def request_signature(
    secret: str,
    *,
    method: str,
    path: str,
    timestamp: str,
    nonce: str,
    body: bytes,
) -> str:
    canonical = "\n".join(
        (
            method.upper(),
            path,
            timestamp,
            nonce,
            stable_payload_digest(body),
        )
    ).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()


def signature_matches(expected: str, supplied: str) -> bool:
    return hmac.compare_digest(expected, str(supplied or "").strip().casefold())
