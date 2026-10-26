"""Unit tests for RateLimiter's Python-side behavior (result construction,
fail-open/fail-closed handling) using a fake Redis client that reimplements
the token-bucket math in pure Python with the exact same signature as the
real Lua script (gatekeeper/rate_limit/lua/token_bucket.lua).

The Lua script itself is exercised against a real Redis instance in
tests/integration/test_rate_limit_integration.py -- that is the test that
actually proves atomicity/correctness of the production code path.
"""

from __future__ import annotations

import time

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from gatekeeper.errors import DependencyUnavailableError
from gatekeeper.rate_limit.limiter import RateLimiter
from gatekeeper.routing_config import TierPolicy


class FakeScript:
    """Mirrors token_bucket.lua's behavior for a single in-memory bucket store."""

    def __init__(self, store: dict, should_fail: bool = False):
        self._store = store
        self.should_fail = False

    async def __call__(self, keys, args):
        if self.should_fail:
            raise RedisConnectionError("simulated redis outage")

        key = keys[0]
        capacity, refill_rate, burst, now_ms, cost, ttl_seconds = args
        max_tokens = capacity + burst

        tokens, last_ts = self._store.get(key, (max_tokens, now_ms))
        elapsed_ms = max(0, now_ms - last_ts)
        refilled = min(max_tokens, tokens + (elapsed_ms / 1000.0) * refill_rate)

        if refilled >= cost:
            allowed = 1
            refilled -= cost
            retry_after_ms = 0
        else:
            allowed = 0
            deficit = cost - refilled
            retry_after_ms = int((deficit / refill_rate) * 1000) if refill_rate > 0 else ttl_seconds * 1000

        self._store[key] = (refilled, now_ms)
        return [allowed, int(refilled), retry_after_ms]


class FakeRedisClient:
    def __init__(self, should_fail: bool = False):
        self._store: dict = {}
        self.should_fail = should_fail
        self._script: FakeScript | None = None

    def register_script(self, _source: str) -> FakeScript:
        self._script = FakeScript(self._store)
        self._script.should_fail = self.should_fail
        return self._script


@pytest.fixture
def policy() -> TierPolicy:
    return TierPolicy(capacity=5, refill_rate_per_sec=1.0, burst=0)


async def test_first_requests_up_to_capacity_are_allowed(policy):
    limiter = RateLimiter(FakeRedisClient(), fail_mode="fail_closed")
    for _ in range(5):
        result = await limiter.check("bucket:a", policy)
        assert result.allowed is True
    result = await limiter.check("bucket:a", policy)
    assert result.allowed is False


async def test_remaining_decreases_with_each_allowed_request(policy):
    limiter = RateLimiter(FakeRedisClient(), fail_mode="fail_closed")
    first = await limiter.check("bucket:b", policy)
    second = await limiter.check("bucket:b", policy)
    assert second.remaining < first.remaining


async def test_separate_keys_have_independent_buckets(policy):
    limiter = RateLimiter(FakeRedisClient(), fail_mode="fail_closed")
    for _ in range(5):
        await limiter.check("bucket:client-1", policy)
    denied = await limiter.check("bucket:client-1", policy)
    allowed_other_client = await limiter.check("bucket:client-2", policy)
    assert denied.allowed is False
    assert allowed_other_client.allowed is True


async def test_retry_after_is_positive_when_denied(policy):
    limiter = RateLimiter(FakeRedisClient(), fail_mode="fail_closed")
    for _ in range(5):
        await limiter.check("bucket:c", policy)
    result = await limiter.check("bucket:c", policy)
    assert result.allowed is False
    assert result.retry_after_seconds > 0


async def test_fail_closed_raises_dependency_unavailable_on_redis_error(policy):
    limiter = RateLimiter(FakeRedisClient(should_fail=True), fail_mode="fail_closed")
    with pytest.raises(DependencyUnavailableError):
        await limiter.check("bucket:d", policy)


async def test_fail_open_allows_traffic_through_on_redis_error(policy):
    limiter = RateLimiter(FakeRedisClient(should_fail=True), fail_mode="fail_open")
    result = await limiter.check("bucket:e", policy)
    assert result.allowed is True
    assert result.degraded is True


async def test_burst_allowance_increases_effective_capacity():
    burst_policy = TierPolicy(capacity=2, refill_rate_per_sec=0.001, burst=3)
    limiter = RateLimiter(FakeRedisClient(), fail_mode="fail_closed")
    allowed_count = 0
    for _ in range(5):
        result = await limiter.check("bucket:burst", burst_policy)
        if result.allowed:
            allowed_count += 1
    assert allowed_count == 5  # capacity(2) + burst(3)
    denied = await limiter.check("bucket:burst", burst_policy)
    assert denied.allowed is False


async def test_tokens_refill_over_time():
    policy = TierPolicy(capacity=1, refill_rate_per_sec=1000.0, burst=0)
    limiter = RateLimiter(FakeRedisClient(), fail_mode="fail_closed")
    first = await limiter.check("bucket:refill", policy)
    assert first.allowed is True
    second = await limiter.check("bucket:refill", policy)
    assert second.allowed is False
    time.sleep(0.01)  # 1000 tokens/sec * 10ms => ~10 tokens refilled
    third = await limiter.check("bucket:refill", policy)
    assert third.allowed is True
