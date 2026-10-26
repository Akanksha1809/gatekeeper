"""Retry policy for upstream calls.

Only requests that are safe to repeat are retried:
  - the HTTP method is idempotent (GET, HEAD, OPTIONS, PUT, DELETE), AND
  - the route's retry policy is enabled, AND
  - the failure looks transient (connection error, timeout, or a 503/504
    from the upstream) -- we never retry on a 4xx, since that's the
    upstream telling us the *request* was bad, not that it's temporarily
    unavailable.

POST/PATCH are never retried automatically, because without an
idempotency-key mechanism (out of scope here, see docs/architecture.md)
replaying them could duplicate a side effect. This also keeps retries from
interacting badly with rate limits: every retry attempt still consumes
only the one rate-limit token charged before the first attempt, and the
max_attempts cap bounds how many extra upstream calls one client request
can generate, preventing retry storms from amplifying load during an
upstream incident.

Backoff is exponential with full jitter: `uniform(0, min(backoff_max, base * 2**attempt))`.
"""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable, Sequence

from gatekeeper.routing_config import IDEMPOTENT_METHODS, RetryPolicy

RETRYABLE_STATUS_CODES: Sequence[int] = (502, 503, 504)


class RetryableError(Exception):
    """Raised by the caller's operation to signal a transient failure worth retrying."""


def is_retryable_method(method: str) -> bool:
    return method.upper() in IDEMPOTENT_METHODS


def compute_backoff_seconds(attempt: int, policy: RetryPolicy, rng: random.Random | None = None) -> float:
    """attempt is 0-indexed (0 = delay before the 2nd try)."""
    uniform = rng.uniform if rng is not None else random.uniform
    ceiling = min(policy.backoff_max_seconds, policy.backoff_base_seconds * (2**attempt))
    return uniform(0, ceiling)


async def run_with_retry(
    operation: Callable[[int], Awaitable[object]],
    *,
    method: str,
    policy: RetryPolicy,
    sleep: Callable[[float], Awaitable[None]],
    rng: random.Random | None = None,
    on_retry: Callable[[int, Exception], None] | None = None,
) -> object:
    """Run `operation(attempt_index)` with retries per `policy`.

    `operation` should raise RetryableError for failures worth retrying, and
    any other exception (or return normally) to stop immediately.
    """
    attempts_allowed = policy.max_attempts if (policy.enabled and is_retryable_method(method)) else 1

    last_exc: Exception | None = None
    for attempt in range(attempts_allowed):
        try:
            return await operation(attempt)
        except RetryableError as exc:
            last_exc = exc
            if attempt + 1 >= attempts_allowed:
                break
            if on_retry:
                on_retry(attempt, exc)
            delay = compute_backoff_seconds(attempt, policy, rng)
            await sleep(delay)
    assert last_exc is not None
    raise last_exc
