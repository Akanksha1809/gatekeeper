"""Orchestration tests for GatewayService -- route resolution, rate limit
enforcement, and the forward() path (retry + circuit breaker interaction)
-- using fakes for the rate limiter and upstream HTTP client so no network
or Redis is involved.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from gatekeeper.auth.service import AuthenticatedPrincipal
from gatekeeper.errors import (
    CircuitOpenError,
    MethodNotAllowedError,
    RateLimitExceededError,
    RouteNotFoundError,
    UpstreamConnectionError,
)
from gatekeeper.proxy.circuit_breaker import CircuitBreakerRegistry
from gatekeeper.proxy.router import GatewayService, ProxyRequest
from gatekeeper.proxy.upstream import UpstreamResponse
from gatekeeper.routing_config import (
    CircuitBreakerPolicy,
    PoliciesFile,
    RetryPolicy,
    RouteConfig,
    RouteTable,
    TierPolicy,
    TimeoutPolicy,
)


def make_route(**overrides) -> RouteConfig:
    defaults = {
        "name": "users",
        "prefix": "/api/users",
        "upstream_url": "http://backend:9001",
        "strip_prefix": True,
        "methods": ["GET", "POST"],
        "timeout_seconds": TimeoutPolicy(connect=1, read=1, write=1, pool=1),
        "retry": RetryPolicy(
            enabled=True, max_attempts=3, backoff_base_seconds=0.001, backoff_max_seconds=0.01
        ),
        "circuit_breaker": CircuitBreakerPolicy(
            failure_threshold=2, cooldown_seconds=30, half_open_max_calls=1
        ),
        "rate_limit_scope": "default",
    }
    defaults.update(overrides)
    return RouteConfig(**defaults)


def make_policies() -> PoliciesFile:
    return PoliciesFile(
        tiers={
            "anonymous": TierPolicy(capacity=1, refill_rate_per_sec=1, burst=0),
            "standard": TierPolicy(capacity=100, refill_rate_per_sec=10, burst=0),
        },
        route_scopes={"default": TierPolicy(capacity=100, refill_rate_per_sec=10, burst=0)},
    )


@dataclass
class FakeRateLimitResult:
    allowed: bool
    limit: int = 100
    remaining: int = 99
    retry_after_seconds: float = 0.0


@dataclass
class FakeRateLimiter:
    allowed: bool = True

    async def check(self, key, policy):
        return FakeRateLimitResult(allowed=self.allowed, limit=policy.capacity)


@dataclass
class FakeUpstreamClient:
    responses: list = field(default_factory=list)  # list of UpstreamResponse | Exception
    calls: int = 0

    async def send(self, **kwargs):
        self.calls += 1
        item = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


def make_service(route_table, policies, rate_limiter, upstream_client) -> GatewayService:
    return GatewayService(
        route_table=route_table,
        policies=policies,
        rate_limiter=rate_limiter,
        circuit_registry=CircuitBreakerRegistry(),
        upstream_client=upstream_client,
    )


def ok_response(status_code=200) -> UpstreamResponse:
    return UpstreamResponse(status_code=status_code, headers=[], body=b"{}", elapsed_seconds=0.01)


def make_proxy_request(method="GET") -> ProxyRequest:
    return ProxyRequest(
        method=method, path="/api/users/1", headers={}, query_params=[], body=b"", request_id="req-1"
    )


PRINCIPAL = AuthenticatedPrincipal(client_id="c1", client_name="acme", tier="standard", api_key_id="k1")


def default_service() -> GatewayService:
    return make_service(RouteTable([make_route()]), make_policies(), FakeRateLimiter(), FakeUpstreamClient())


class TestResolveRoute:
    def test_resolves_known_route(self):
        service = default_service()
        route, remainder = service.resolve_route("/api/users/42", "GET")
        assert route.name == "users"
        assert remainder == "/42"

    def test_unknown_route_raises(self):
        service = default_service()
        with pytest.raises(RouteNotFoundError):
            service.resolve_route("/api/unknown", "GET")

    def test_disallowed_method_raises(self):
        service = default_service()
        with pytest.raises(MethodNotAllowedError):
            service.resolve_route("/api/users/42", "DELETE")


class TestEnforceRateLimits:
    async def test_allowed_passes_through(self):
        service = make_service(
            RouteTable([make_route()]), make_policies(), FakeRateLimiter(allowed=True), FakeUpstreamClient()
        )
        headers = await service.enforce_rate_limits(make_route(), PRINCIPAL)
        assert headers.limit > 0

    async def test_denied_raises_429_with_retry_after(self):
        service = make_service(
            RouteTable([make_route()]), make_policies(), FakeRateLimiter(allowed=False), FakeUpstreamClient()
        )
        with pytest.raises(RateLimitExceededError) as exc_info:
            await service.enforce_rate_limits(make_route(), PRINCIPAL)
        assert "Retry-After" in exc_info.value.headers

    async def test_unauthenticated_uses_anonymous_tier(self):
        service = make_service(
            RouteTable([make_route()]), make_policies(), FakeRateLimiter(allowed=True), FakeUpstreamClient()
        )
        headers = await service.enforce_rate_limits(make_route(), None)
        assert headers.limit > 0


class TestForward:
    async def test_successful_call_returns_response(self):
        upstream = FakeUpstreamClient(responses=[ok_response()])
        service = make_service(RouteTable([make_route()]), make_policies(), FakeRateLimiter(), upstream)
        response = await service.forward(make_route(), "/1", make_proxy_request())
        assert response.status_code == 200
        assert upstream.calls == 1

    async def test_retries_on_503_then_succeeds(self):
        upstream = FakeUpstreamClient(responses=[ok_response(503), ok_response(200)])
        route = make_route(
            retry=RetryPolicy(
                enabled=True, max_attempts=3, backoff_base_seconds=0.001, backoff_max_seconds=0.005
            )
        )
        service = make_service(RouteTable([route]), make_policies(), FakeRateLimiter(), upstream)
        response = await service.forward(route, "/1", make_proxy_request())
        assert response.status_code == 200
        assert upstream.calls == 2

    async def test_non_idempotent_method_is_not_retried(self):
        upstream = FakeUpstreamClient(responses=[ok_response(503)])
        route = make_route(retry=RetryPolicy(enabled=True, max_attempts=3))
        service = make_service(RouteTable([route]), make_policies(), FakeRateLimiter(), upstream)
        with pytest.raises(UpstreamConnectionError):
            await service.forward(route, "/1", make_proxy_request(method="POST"))
        assert upstream.calls == 1

    async def test_circuit_opens_after_threshold_and_rejects_without_calling_upstream(self):
        upstream = FakeUpstreamClient(responses=[ok_response(503)] * 10)
        route = make_route(
            circuit_breaker=CircuitBreakerPolicy(
                failure_threshold=2, cooldown_seconds=30, half_open_max_calls=1
            ),
            retry=RetryPolicy(enabled=False, max_attempts=1),
        )
        service = make_service(RouteTable([route]), make_policies(), FakeRateLimiter(), upstream)

        with pytest.raises(UpstreamConnectionError):
            await service.forward(route, "/1", make_proxy_request())
        with pytest.raises(UpstreamConnectionError):
            await service.forward(route, "/1", make_proxy_request())

        calls_before = upstream.calls
        with pytest.raises(CircuitOpenError):
            await service.forward(route, "/1", make_proxy_request())
        assert upstream.calls == calls_before  # circuit rejected before hitting upstream
