import pytest
from pydantic import ValidationError

from gatekeeper.routing_config import (
    ConfigError,
    PoliciesFile,
    RouteConfig,
    RouteTable,
    load_policies,
    load_routes,
)

VALID_ROUTE_KWARGS = {
    "name": "users",
    "prefix": "/api/users",
    "upstream_url": "http://healthy-service:9001",
    "strip_prefix": True,
    "methods": ["GET", "POST"],
    "timeout_seconds": {"connect": 1, "read": 1, "write": 1, "pool": 1},
    "retry": {
        "enabled": True,
        "max_attempts": 3,
        "backoff_base_seconds": 0.1,
        "backoff_max_seconds": 1,
    },
    "circuit_breaker": {"failure_threshold": 5, "cooldown_seconds": 10, "half_open_max_calls": 2},
    "rate_limit_scope": "default",
}


def test_load_routes_from_the_real_config_file():
    table = load_routes("config/routes.yaml")
    assert len(table.routes) == 4
    names = {r.name for r in table.routes}
    assert {"users", "orders", "demo-slow", "demo-flaky"} <= names


def test_load_policies_from_the_real_config_file():
    policies = load_policies("config/policies.yaml")
    assert "anonymous" in policies.tiers
    assert "free" in policies.tiers
    assert "default" in policies.route_scopes


def test_load_routes_missing_file_raises_config_error():
    with pytest.raises(ConfigError):
        load_routes("config/does-not-exist.yaml")


def test_route_prefix_must_start_with_slash():
    with pytest.raises(ValidationError):
        RouteConfig(**{**VALID_ROUTE_KWARGS, "prefix": "api/users"})


def test_route_rejects_unsupported_methods():
    with pytest.raises(ValidationError):
        RouteConfig(**{**VALID_ROUTE_KWARGS, "methods": ["TRACE"]})


def test_route_upstream_url_must_be_http():
    with pytest.raises(ValidationError):
        RouteConfig(**{**VALID_ROUTE_KWARGS, "upstream_url": "ftp://backend"})


def test_policies_file_requires_anonymous_tier():
    with pytest.raises(ValidationError):
        PoliciesFile.model_validate(
            {
                "tiers": {"free": {"capacity": 10, "refill_rate_per_sec": 1, "burst": 0}},
                "route_scopes": {},
            }
        )


class TestRouteTableResolution:
    def setup_method(self):
        users = RouteConfig(**VALID_ROUTE_KWARGS)
        demo = RouteConfig(**{**VALID_ROUTE_KWARGS, "name": "demo", "prefix": "/api/demo"})
        demo_slow = RouteConfig(**{**VALID_ROUTE_KWARGS, "name": "demo-slow", "prefix": "/api/demo/slow"})
        self.table = RouteTable([users, demo, demo_slow])

    def test_exact_prefix_match(self):
        resolved = self.table.resolve("/api/users")
        assert resolved is not None
        route, remainder = resolved
        assert route.name == "users"
        assert remainder == "/"

    def test_prefix_with_subpath_strips_prefix(self):
        resolved = self.table.resolve("/api/users/42")
        assert resolved is not None
        route, remainder = resolved
        assert route.name == "users"
        assert remainder == "/42"

    def test_longest_prefix_wins_over_shorter_overlapping_prefix(self):
        resolved = self.table.resolve("/api/demo/slow/ping")
        assert resolved is not None
        route, remainder = resolved
        assert route.name == "demo-slow"
        assert remainder == "/ping"

    def test_unknown_path_returns_none(self):
        assert self.table.resolve("/nope") is None

    def test_partial_prefix_match_is_not_a_match(self):
        # "/api/userssuffix" must NOT match the "/api/users" route.
        assert self.table.resolve("/api/userssuffix") is None
