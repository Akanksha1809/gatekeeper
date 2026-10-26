"""Orchestrates one proxied request: route resolution, rate limiting, the
circuit breaker, and the retrying upstream call. This is the core of the
gateway and is deliberately framework-agnostic (it doesn't import FastAPI)
so it can be unit tested directly.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from gatekeeper import metrics
from gatekeeper.auth.service import AuthenticatedPrincipal
from gatekeeper.errors import (
    CircuitOpenError,
    MethodNotAllowedError,
    RateLimitExceededError,
    RouteNotFoundError,
    UpstreamConnectionError,
    UpstreamTimeoutError,
)
from gatekeeper.logging_setup import log_with_fields
from gatekeeper.proxy.circuit_breaker import CircuitBreakerRegistry
from gatekeeper.proxy.retry import RetryableError, run_with_retry
from gatekeeper.proxy.upstream import UpstreamClient, UpstreamResponse
from gatekeeper.rate_limit.limiter import RateLimiter
from gatekeeper.routing_config import PoliciesFile, RouteConfig, RouteTable

logger = logging.getLogger("gatekeeper.proxy")


@dataclass(frozen=True)
class ProxyRequest:
    method: str
    path: str
    headers: dict[str, str]
    query_params: list[tuple[str, str]]
    body: bytes
    request_id: str


@dataclass(frozen=True)
class RateLimitHeaders:
    limit: int
    remaining: int
    retry_after: float


class GatewayService:
    def __init__(
        self,
        route_table: RouteTable,
        policies: PoliciesFile,
        rate_limiter: RateLimiter,
        circuit_registry: CircuitBreakerRegistry,
        upstream_client: UpstreamClient,
    ):
        self.route_table = route_table
        self.policies = policies
        self.rate_limiter = rate_limiter
        self.circuit_registry = circuit_registry
        self.upstream_client = upstream_client

    def resolve_route(self, path: str, method: str) -> tuple[RouteConfig, str]:
        resolved = self.route_table.resolve(path)
        if resolved is None:
            raise RouteNotFoundError(f"No route configured for path {path!r}")
        route, remainder = resolved
        if method.upper() not in route.methods:
            raise MethodNotAllowedError(f"Method {method} not allowed on route {route.name!r}")
        return route, remainder

    async def enforce_rate_limits(
        self, route: RouteConfig, principal: AuthenticatedPrincipal | None
    ) -> RateLimitHeaders:
        tier_name = principal.tier if principal else "anonymous"
        tier_policy = self.policies.tiers.get(tier_name)
        if tier_policy is None:
            tier_policy = self.policies.tiers["anonymous"]
        bucket_subject = principal.api_key_id if principal else "anonymous"

        tier_key = f"ratelimit:tier:{bucket_subject}"
        tier_result = await self.rate_limiter.check(tier_key, tier_policy)
        if not tier_result.allowed:
            metrics.RATE_LIMIT_REJECTIONS.labels(route=route.name, tier=tier_name).inc()
            raise RateLimitExceededError(
                "Rate limit exceeded for this API key",
                headers={"Retry-After": str(max(1, int(tier_result.retry_after_seconds)))},
            )

        route_policy = self.policies.route_scopes.get(route.rate_limit_scope)
        if route_policy is not None:
            route_key = f"ratelimit:route:{bucket_subject}:{route.rate_limit_scope}"
            route_result = await self.rate_limiter.check(route_key, route_policy)
            if not route_result.allowed:
                metrics.RATE_LIMIT_REJECTIONS.labels(route=route.name, tier=tier_name).inc()
                raise RateLimitExceededError(
                    "Rate limit exceeded for this route",
                    headers={"Retry-After": str(max(1, int(route_result.retry_after_seconds)))},
                )
            return RateLimitHeaders(
                limit=route_result.limit,
                remaining=min(tier_result.remaining, route_result.remaining),
                retry_after=0.0,
            )

        return RateLimitHeaders(limit=tier_result.limit, remaining=tier_result.remaining, retry_after=0.0)

    async def forward(self, route: RouteConfig, remainder_path: str, req: ProxyRequest) -> UpstreamResponse:
        breaker = self.circuit_registry.get_or_create(
            route.name,
            failure_threshold=route.circuit_breaker.failure_threshold,
            cooldown_seconds=route.circuit_breaker.cooldown_seconds,
            half_open_max_calls=route.circuit_breaker.half_open_max_calls,
        )
        if not breaker.allow_request():
            metrics.CIRCUIT_BREAKER_REJECTIONS.labels(route=route.name).inc()
            raise CircuitOpenError(f"Upstream {route.name!r} is temporarily unavailable (circuit open)")

        target_url = route.upstream_url + remainder_path

        async def attempt(attempt_index: int) -> UpstreamResponse:
            start = time.monotonic()
            try:
                response = await self.upstream_client.send(
                    method=req.method,
                    url=target_url,
                    headers=req.headers,
                    params=req.query_params,
                    body=req.body,
                    timeout=route.timeout_seconds,
                    request_id=req.request_id,
                )
            except RetryableError:
                metrics.UPSTREAM_LATENCY.labels(route=route.name).observe(time.monotonic() - start)
                raise
            metrics.UPSTREAM_LATENCY.labels(route=route.name).observe(time.monotonic() - start)
            if response.status_code in (502, 503, 504):
                raise RetryableError(f"Upstream returned {response.status_code}")
            return response

        def on_retry(attempt_index: int, exc: Exception) -> None:
            metrics.RETRY_ATTEMPTS.labels(route=route.name).inc()
            log_with_fields(
                logger,
                logging.WARNING,
                "retrying upstream request",
                route=route.name,
                attempt=attempt_index,
                request_id=req.request_id,
            )

        try:
            import asyncio

            result = await run_with_retry(
                attempt,
                method=req.method,
                policy=route.retry,
                sleep=asyncio.sleep,
                on_retry=on_retry,
            )
        except RetryableError as exc:
            breaker.record_failure()
            cause = exc.__cause__
            log_with_fields(
                logger,
                logging.WARNING,
                "upstream call failed after retries",
                route=route.name,
                request_id=req.request_id,
                detail=str(exc),
            )
            if isinstance(cause, UpstreamTimeoutError):
                metrics.UPSTREAM_TIMEOUTS.labels(route=route.name).inc()
                raise cause from exc
            if isinstance(cause, UpstreamConnectionError):
                raise cause from exc
            raise UpstreamConnectionError("Upstream service returned an error") from exc
        else:
            breaker.record_success()
            return result  # type: ignore[return-value]
