from __future__ import annotations

import random

import pytest

from gatekeeper.proxy.retry import (
    RetryableError,
    compute_backoff_seconds,
    is_retryable_method,
    run_with_retry,
)
from gatekeeper.routing_config import RetryPolicy


async def _instant_sleep(_seconds: float) -> None:
    return None


def test_idempotent_methods_are_retryable():
    for method in ("GET", "HEAD", "OPTIONS", "PUT", "DELETE"):
        assert is_retryable_method(method) is True


def test_post_and_patch_are_not_retryable():
    assert is_retryable_method("POST") is False
    assert is_retryable_method("PATCH") is False


def test_backoff_is_bounded_by_max_and_never_negative():
    policy = RetryPolicy(backoff_base_seconds=0.1, backoff_max_seconds=2.0, max_attempts=5)
    rng = random.Random(42)
    for attempt in range(10):
        delay = compute_backoff_seconds(attempt, policy, rng)
        assert 0 <= delay <= policy.backoff_max_seconds


async def test_run_with_retry_succeeds_without_retrying_on_first_try():
    calls = []

    async def op(attempt: int):
        calls.append(attempt)
        return "ok"

    result = await run_with_retry(op, method="GET", policy=RetryPolicy(max_attempts=3), sleep=_instant_sleep)
    assert result == "ok"
    assert calls == [0]


async def test_run_with_retry_retries_up_to_max_attempts_then_raises():
    calls = []

    async def op(attempt: int):
        calls.append(attempt)
        raise RetryableError("boom")

    with pytest.raises(RetryableError):
        await run_with_retry(op, method="GET", policy=RetryPolicy(max_attempts=3), sleep=_instant_sleep)
    assert calls == [0, 1, 2]


async def test_run_with_retry_succeeds_on_a_later_attempt():
    calls = []

    async def op(attempt: int):
        calls.append(attempt)
        if attempt < 2:
            raise RetryableError("transient")
        return "recovered"

    result = await run_with_retry(op, method="GET", policy=RetryPolicy(max_attempts=5), sleep=_instant_sleep)
    assert result == "recovered"
    assert calls == [0, 1, 2]


async def test_non_idempotent_method_is_never_retried_even_if_policy_enabled():
    calls = []

    async def op(attempt: int):
        calls.append(attempt)
        raise RetryableError("boom")

    with pytest.raises(RetryableError):
        await run_with_retry(
            op, method="POST", policy=RetryPolicy(enabled=True, max_attempts=5), sleep=_instant_sleep
        )
    assert calls == [0]  # exactly one attempt, no retries


async def test_retry_disabled_in_policy_means_single_attempt():
    calls = []

    async def op(attempt: int):
        calls.append(attempt)
        raise RetryableError("boom")

    with pytest.raises(RetryableError):
        await run_with_retry(
            op, method="GET", policy=RetryPolicy(enabled=False, max_attempts=5), sleep=_instant_sleep
        )
    assert calls == [0]


async def test_non_retryable_exception_propagates_immediately():
    calls = []

    async def op(attempt: int):
        calls.append(attempt)
        raise ValueError("not retryable")

    with pytest.raises(ValueError):
        await run_with_retry(op, method="GET", policy=RetryPolicy(max_attempts=5), sleep=_instant_sleep)
    assert calls == [0]


async def test_on_retry_callback_invoked_once_per_retry():
    attempts_seen = []

    async def op(attempt: int):
        if attempt < 2:
            raise RetryableError("transient")
        return "ok"

    await run_with_retry(
        op,
        method="GET",
        policy=RetryPolicy(max_attempts=5),
        sleep=_instant_sleep,
        on_retry=lambda attempt, exc: attempts_seen.append(attempt),
    )
    assert attempts_seen == [0, 1]
