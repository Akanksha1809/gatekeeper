"""Redis-backed token-bucket rate limiter.

Algorithm & trade-offs
----------------------
We use a **token bucket** rather than a fixed or sliding-window counter:

- Fixed window counters are simple but allow a 2x burst at window
  boundaries (e.g. a client could send its full quota at 11:59:59 and
  again at 12:00:00). Sliding-window counters fix that but need to store
  a log of timestamps (or an approximation), which is heavier on Redis
  memory for high-volume keys.
- Token bucket gives smooth sustained-rate enforcement (`refill_rate_per_sec`)
  *and* an explicit, separately-tunable burst allowance (`capacity` + `burst`),
  which maps directly onto the tiered-quota requirement in the brief. It
  only needs two numbers (tokens, last-refill-timestamp) per key, so memory
  use per client is O(1) regardless of request volume.
- The refill/consume read-modify-write is executed as a single Redis Lua
  script (EVAL), which Redis runs atomically with respect to all other
  clients -- this is what keeps multiple gateway replicas from each
  independently granting the full quota (a plain GET+SET from Python would
  race under concurrency).

Fail-open vs fail-closed
-------------------------
If Redis is unreachable, two conservative behaviors are supported:
  - fail_closed (default): reject the request (503) rather than risk an
    upstream being overwhelmed by unbounded traffic. Safety-biased.
  - fail_open: let the request through without rate limiting. Availability-
    biased; only use this if upstream services can absorb unmetered load.
This is controlled by GATEKEEPER_RATE_LIMIT_FAIL_MODE.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import redis.asyncio as redis_asyncio
from redis.exceptions import RedisError

from gatekeeper.errors import DependencyUnavailableError
from gatekeeper.routing_config import TierPolicy

_LUA_PATH = Path(__file__).parent / "lua" / "token_bucket.lua"

# Idle buckets expire after this long with no traffic, bounding Redis memory
# growth from clients that show up once and never return.
_BUCKET_TTL_SECONDS = 3600


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: float
    degraded: bool = False  # true if this result was produced during a Redis outage


class RateLimiter:
    def __init__(self, client: redis_asyncio.Redis, fail_mode: str = "fail_closed"):
        self._client = client
        self._fail_mode = fail_mode
        self._script = client.register_script(_LUA_PATH.read_text())

    async def check(self, key: str, policy: TierPolicy, cost: int = 1) -> RateLimitResult:
        now_ms = int(time.time() * 1000)
        try:
            allowed, remaining, retry_after_ms = await self._script(
                keys=[key],
                args=[
                    policy.capacity,
                    policy.refill_rate_per_sec,
                    policy.burst,
                    now_ms,
                    cost,
                    _BUCKET_TTL_SECONDS,
                ],
            )
        except RedisError as exc:
            return self._on_redis_error(policy, exc)

        return RateLimitResult(
            allowed=bool(allowed),
            limit=policy.capacity + policy.burst,
            remaining=int(remaining),
            retry_after_seconds=round(int(retry_after_ms) / 1000.0, 3),
        )

    def _on_redis_error(self, policy: TierPolicy, exc: Exception) -> RateLimitResult:
        if self._fail_mode == "fail_open":
            return RateLimitResult(
                allowed=True,
                limit=policy.capacity + policy.burst,
                remaining=policy.capacity + policy.burst,
                retry_after_seconds=0.0,
                degraded=True,
            )
        raise DependencyUnavailableError("Rate limiting backend (Redis) is unavailable") from exc


async def check_connectivity(client: redis_asyncio.Redis) -> bool:
    try:
        await client.ping()
        return True
    except RedisError:
        return False
