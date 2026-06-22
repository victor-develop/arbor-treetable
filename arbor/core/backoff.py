"""Webhook retry backoff schedule (ARCHITECTURE §7, DATA-MODEL §11).

Default: 6 attempts over ~24h at offsets 0s, 30s, 5m, 30m, 2h, 12h. Pure
functions over integer seconds; the frappe dispatcher adds the wall-clock base
time and optional jitter. Keeping this pure makes backoff deterministically
testable with a freezable clock.
"""

from __future__ import annotations

#: Delay (seconds) BEFORE attempt N (1-indexed), measured from the first try.
RETRY_SCHEDULE_SECONDS: tuple[int, ...] = (
    0,  # attempt 1: immediate
    30,  # attempt 2: +30s
    5 * 60,  # attempt 3: +5m
    30 * 60,  # attempt 4: +30m
    2 * 60 * 60,  # attempt 5: +2h
    12 * 60 * 60,  # attempt 6: +12h
)

MAX_ATTEMPTS: int = len(RETRY_SCHEDULE_SECONDS)


def delay_for_attempt(attempt: int) -> int:
    """Seconds to wait before ``attempt`` (1-indexed). Raises for attempts past
    the schedule (the delivery is ``exhausted``)."""
    if attempt < 1:
        raise ValueError("attempt is 1-indexed")
    if attempt > MAX_ATTEMPTS:
        raise ValueError(f"attempt {attempt} exceeds MAX_ATTEMPTS={MAX_ATTEMPTS} (exhausted)")
    return RETRY_SCHEDULE_SECONDS[attempt - 1]


def next_retry_offset(current_attempt: int) -> int | None:
    """Seconds from the FIRST attempt until the next retry after
    ``current_attempt`` completes, or ``None`` if exhausted."""
    next_attempt = current_attempt + 1
    if next_attempt > MAX_ATTEMPTS:
        return None
    return RETRY_SCHEDULE_SECONDS[next_attempt - 1]


def is_exhausted(attempts: int) -> bool:
    """True once ``attempts`` reaches the schedule length (no retry remains)."""
    return attempts >= MAX_ATTEMPTS
