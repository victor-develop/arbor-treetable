"""HMAC determinism + verification, and the webhook backoff schedule
(ARCHITECTURE §7)."""

from __future__ import annotations

import pytest

from arbor.core.backoff import (
    MAX_ATTEMPTS,
    RETRY_SCHEDULE_SECONDS,
    delay_for_attempt,
    is_exhausted,
    next_retry_offset,
)
from arbor.core.security import compute_signature, verify_signature


def test_hmac_is_deterministic_and_prefixed():
    sig1 = compute_signature("s3cr3t", b'{"event_id":"evt-1"}')
    sig2 = compute_signature("s3cr3t", b'{"event_id":"evt-1"}')
    assert sig1 == sig2
    assert sig1.startswith("sha256=")
    assert len(sig1) == len("sha256=") + 64


def test_hmac_changes_with_secret_and_body():
    body = b"payload"
    assert compute_signature("a", body) != compute_signature("b", body)
    assert compute_signature("a", b"x") != compute_signature("a", b"y")


def test_hmac_accepts_str_and_bytes_equivalently():
    assert compute_signature("k", "hello") == compute_signature("k", b"hello")


def test_verify_signature_roundtrip():
    sig = compute_signature("k", b"body")
    assert verify_signature("k", b"body", sig) is True
    assert verify_signature("k", b"tampered", sig) is False
    assert verify_signature("wrong", b"body", sig) is False
    assert verify_signature("k", b"body", "") is False


def test_backoff_schedule_exact():
    assert RETRY_SCHEDULE_SECONDS == (0, 30, 300, 1800, 7200, 43200)
    assert MAX_ATTEMPTS == 6


def test_delay_for_attempt():
    assert delay_for_attempt(1) == 0
    assert delay_for_attempt(2) == 30
    assert delay_for_attempt(6) == 43200
    with pytest.raises(ValueError):
        delay_for_attempt(7)
    with pytest.raises(ValueError):
        delay_for_attempt(0)


def test_next_retry_offset_and_exhaustion():
    assert next_retry_offset(1) == 30
    assert next_retry_offset(5) == 43200
    assert next_retry_offset(6) is None
    assert is_exhausted(6) is True
    assert is_exhausted(5) is False
