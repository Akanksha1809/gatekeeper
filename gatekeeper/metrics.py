"""Prometheus metrics definitions.

All labels are bounded-cardinality by construction:
  - `route` is the configured route *name* (e.g. "users"), never the raw
    request path or an upstream URL.
  - `method` is the HTTP method.
  - `status_class` is "2xx"/"4xx"/"5xx" etc, never the raw status code's
    uninterned long tail, and never the full status code combined with path.
No metric is labeled with an API key, client ID, request ID, or raw URL.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

REQUEST_COUNT = Counter(
    "gatekeeper_requests_total",
    "Total requests handled by the gateway",
    ["method", "route", "status_class"],
)

REQUEST_LATENCY = Histogram(
    "gatekeeper_request_duration_seconds",
    "End-to-end request latency as seen by the gateway",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

UPSTREAM_LATENCY = Histogram(
    "gatekeeper_upstream_duration_seconds",
    "Latency of the call to the upstream service (per attempt)",
    ["route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

RATE_LIMIT_REJECTIONS = Counter(
    "gatekeeper_rate_limit_rejections_total",
    "Requests rejected due to rate limiting",
    ["route", "tier"],
)

AUTH_FAILURES = Counter(
    "gatekeeper_auth_failures_total",
    "Authentication failures",
    ["reason"],
)

UPSTREAM_TIMEOUTS = Counter(
    "gatekeeper_upstream_timeouts_total",
    "Upstream requests that timed out",
    ["route"],
)

RETRY_ATTEMPTS = Counter(
    "gatekeeper_retry_attempts_total",
    "Retry attempts made against upstreams",
    ["route"],
)

CIRCUIT_BREAKER_STATE = Gauge(
    "gatekeeper_circuit_breaker_state",
    "Current circuit breaker state (0=closed, 1=half_open, 2=open)",
    ["route"],
)

CIRCUIT_BREAKER_TRANSITIONS = Counter(
    "gatekeeper_circuit_breaker_transitions_total",
    "Circuit breaker state transitions",
    ["route", "to_state"],
)

CIRCUIT_BREAKER_REJECTIONS = Counter(
    "gatekeeper_circuit_breaker_rejections_total",
    "Requests rejected because the circuit breaker was open",
    ["route"],
)

IN_FLIGHT_REQUESTS = Gauge(
    "gatekeeper_in_flight_requests",
    "Requests currently being processed by the gateway",
)

DEPENDENCY_UP = Gauge(
    "gatekeeper_dependency_up",
    "Whether a dependency is currently reachable (1) or not (0)",
    ["dependency"],
)

_STATE_TO_GAUGE_VALUE = {"closed": 0, "half_open": 1, "open": 2}


def status_class(status_code: int) -> str:
    return f"{status_code // 100}xx"


def set_circuit_state_metric(route: str, state: str) -> None:
    CIRCUIT_BREAKER_STATE.labels(route=route).set(_STATE_TO_GAUGE_VALUE.get(state, -1))
