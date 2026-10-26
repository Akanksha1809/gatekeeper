"""Integration tests for the real Redis-backed token-bucket limiter --
exercises the actual Lua script (gatekeeper/rate_limit/lua/token_bucket.lua)
against a real Redis instance, which is the thing that actually proves
atomicity under concurrency (something a Python-side mock cannot prove).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from gatekeeper.rate_limit.limiter import RateLimiter
from gatekeeper.routing_config import TierPolicy


@pytest.fixture
def limiter(redis_client):
    return RateLimiter(redis_client, fail_mode="fail_closed")


def unique_key() -> str:
    return f"test:{uuid.uuid4()}"


async def test_normal_traffic_within_quota_is_allowed(limiter):
    policy = TierPolicy(capacity=10, refill_rate_per_sec=1, burst=0)
    key = unique_key()
    for _ in range(10):
        result = await limiter.check(key, policy)
        assert result.allowed is True


async def test_quota_exhaustion_returns_429_equivalent(limiter):
    policy = TierPolicy(capacity=5, refill_rate_per_sec=0.001, burst=0)
    key = unique_key()
    for _ in range(5):
        assert (await limiter.check(key, policy)).allowed is True
    denied = await limiter.check(key, policy)
    assert denied.allowed is False
    assert denied.retry_after_seconds > 0


async def test_burst_allowance_grants_extra_headroom(limiter):
    policy = TierPolicy(capacity=3, refill_rate_per_sec=0.001, burst=4)
    key = unique_key()
    allowed = [await limiter.check(key, policy) for _ in range(7)]
    assert all(r.allowed for r in allowed)
    assert (await limiter.check(key, policy)).allowed is False


async def test_window_rollover_refills_tokens_over_real_time(limiter):
    policy = TierPolicy(capacity=2, refill_rate_per_sec=20, burst=0)  # fast refill for a quick test
    key = unique_key()
    assert (await limiter.check(key, policy)).allowed is True
    assert (await limiter.check(key, policy)).allowed is True
    assert (await limiter.check(key, policy)).allowed is False

    await asyncio.sleep(0.15)  # 20 tokens/sec * 0.15s = 3 tokens refilled (capped at capacity)

    assert (await limiter.check(key, policy)).allowed is True


async def test_concurrent_requests_never_exceed_the_configured_quota(limiter):
    """The key correctness property: N gateway "replicas" (simulated here as
    concurrent coroutines sharing one Redis) hammering the same bucket must
    never collectively admit more than `capacity + burst` requests. A naive
    GET-then-SET implementation would race and over-admit; the atomic Lua
    script must not.
    """
    policy = TierPolicy(capacity=20, refill_rate_per_sec=0.001, burst=0)
    key = unique_key()

    results = await asyncio.gather(*[limiter.check(key, policy) for _ in range(100)])
    allowed_count = sum(1 for r in results if r.allowed)

    assert allowed_count == 20


async def test_separate_api_keys_have_independent_quotas(limiter):
    policy = TierPolicy(capacity=2, refill_rate_per_sec=0.001, burst=0)
    key_a, key_b = unique_key(), unique_key()

    for _ in range(2):
        assert (await limiter.check(key_a, policy)).allowed is True
    assert (await limiter.check(key_a, policy)).allowed is False

    # key_b's bucket is untouched by key_a's exhaustion.
    assert (await limiter.check(key_b, policy)).allowed is True


async def test_separate_routes_have_independent_quotas(limiter):
    policy = TierPolicy(capacity=1, refill_rate_per_sec=0.001, burst=0)
    client_id = uuid.uuid4()
    route_a_key = f"ratelimit:route:{client_id}:route-a"
    route_b_key = f"ratelimit:route:{client_id}:route-b"

    assert (await limiter.check(route_a_key, policy)).allowed is True
    assert (await limiter.check(route_a_key, policy)).allowed is False
    assert (await limiter.check(route_b_key, policy)).allowed is True


async def test_fail_closed_rejects_when_redis_is_unreachable():
    import redis.asyncio as redis_asyncio

    from gatekeeper.errors import DependencyUnavailableError

    unreachable = redis_asyncio.from_url(
        "redis://localhost:1/0", socket_connect_timeout=0.2, socket_timeout=0.2
    )
    limiter = RateLimiter(unreachable, fail_mode="fail_closed")
    policy = TierPolicy(capacity=10, refill_rate_per_sec=1, burst=0)
    with pytest.raises(DependencyUnavailableError):
        await limiter.check(unique_key(), policy)


async def test_fail_open_allows_traffic_when_redis_is_unreachable():
    import redis.asyncio as redis_asyncio

    unreachable = redis_asyncio.from_url(
        "redis://localhost:1/0", socket_connect_timeout=0.2, socket_timeout=0.2
    )
    limiter = RateLimiter(unreachable, fail_mode="fail_open")
    policy = TierPolicy(capacity=10, refill_rate_per_sec=1, burst=0)
    result = await limiter.check(unique_key(), policy)
    assert result.allowed is True
    assert result.degraded is True


async def test_idle_bucket_key_expires_in_redis(redis_client, limiter):
    """Buckets must not grow Redis memory unboundedly -- idle keys expire."""
    policy = TierPolicy(capacity=5, refill_rate_per_sec=1, burst=0)
    key = unique_key()
    await limiter.check(key, policy)
    ttl = await redis_client.ttl(key)
    assert 0 < ttl <= 3600
