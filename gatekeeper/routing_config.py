"""Loading and validation of config/routes.yaml and config/policies.yaml.

Keeping route/policy definitions in one declarative place (rather than
scattered through the codebase) is a hard requirement from the project
brief. This module is the single place that parses those files, validates
them with Pydantic, and fails startup with an actionable error if they are
malformed.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
IDEMPOTENT_METHODS = {"GET", "HEAD", "OPTIONS", "PUT", "DELETE"}


class TimeoutPolicy(BaseModel):
    connect: float = Field(gt=0, le=60)
    read: float = Field(gt=0, le=60)
    write: float = Field(gt=0, le=60)
    pool: float = Field(gt=0, le=60)


class RetryPolicy(BaseModel):
    enabled: bool = True
    max_attempts: int = Field(default=3, ge=1, le=5)
    backoff_base_seconds: float = Field(default=0.1, gt=0, le=5)
    backoff_max_seconds: float = Field(default=2.0, gt=0, le=30)


class CircuitBreakerPolicy(BaseModel):
    failure_threshold: int = Field(default=5, ge=1, le=100)
    cooldown_seconds: float = Field(default=15, gt=0, le=600)
    half_open_max_calls: int = Field(default=2, ge=1, le=20)


class RouteConfig(BaseModel):
    name: str
    prefix: str
    upstream_url: str
    strip_prefix: bool = True
    methods: list[str]
    timeout_seconds: TimeoutPolicy
    retry: RetryPolicy
    circuit_breaker: CircuitBreakerPolicy
    rate_limit_scope: str

    @field_validator("prefix")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError(f"route prefix must start with '/': {v!r}")
        return v.rstrip("/") or "/"

    @field_validator("upstream_url")
    @classmethod
    def _validate_upstream_url(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError(f"upstream_url must be http(s)://...: {v!r}")
        return v.rstrip("/")

    @field_validator("methods")
    @classmethod
    def _validate_methods(cls, v: list[str]) -> list[str]:
        upper = [m.upper() for m in v]
        unknown = set(upper) - ALLOWED_METHODS
        if unknown:
            raise ValueError(f"unsupported HTTP methods in route config: {unknown}")
        if not upper:
            raise ValueError("route must allow at least one HTTP method")
        return upper


class RoutesFile(BaseModel):
    routes: list[RouteConfig]

    @field_validator("routes")
    @classmethod
    def _validate_unique_prefixes(cls, v: list[RouteConfig]) -> list[RouteConfig]:
        seen: dict[str, str] = {}
        for route in v:
            for other_prefix in seen:
                if route.prefix == other_prefix:
                    raise ValueError(f"duplicate route prefix: {route.prefix!r}")
            seen[route.prefix] = route.name
        return v


class TierPolicy(BaseModel):
    capacity: int = Field(ge=1, le=1_000_000)
    refill_rate_per_sec: float = Field(gt=0, le=100_000)
    burst: int = Field(ge=0, le=1_000_000)


class PoliciesFile(BaseModel):
    tiers: dict[str, TierPolicy]
    route_scopes: dict[str, TierPolicy]

    @field_validator("tiers")
    @classmethod
    def _require_anonymous_tier(cls, v: dict[str, TierPolicy]) -> dict[str, TierPolicy]:
        if "anonymous" not in v:
            raise ValueError("policies.yaml must define a 'anonymous' tier for unauthenticated traffic")
        return v


class RouteTable:
    """Resolves an incoming request path to its RouteConfig, longest-prefix-first.

    Longest-prefix match means a more specific route (e.g. /api/demo/slow)
    takes precedence over a broader one (e.g. /api/demo) if both exist.
    """

    def __init__(self, routes: list[RouteConfig]):
        self._routes = sorted(routes, key=lambda r: len(r.prefix), reverse=True)

    @property
    def routes(self) -> list[RouteConfig]:
        return list(self._routes)

    def resolve(self, path: str) -> tuple[RouteConfig, str] | None:
        """Return (route, remainder_path) for the longest matching prefix, or None."""
        for route in self._routes:
            if path == route.prefix or path.startswith(route.prefix + "/"):
                remainder = path[len(route.prefix) :]
                if route.strip_prefix:
                    return route, remainder or "/"
                return route, path
        return None


def load_routes(path: str | Path) -> RouteTable:
    raw = _read_yaml(path)
    try:
        parsed = RoutesFile.model_validate(raw)
    except Exception as exc:  # pragma: no cover - re-raised with context below
        raise ConfigError(f"Invalid routes config at {path}: {exc}") from exc
    return RouteTable(parsed.routes)


def load_policies(path: str | Path) -> PoliciesFile:
    raw = _read_yaml(path)
    try:
        return PoliciesFile.model_validate(raw)
    except Exception as exc:  # pragma: no cover - re-raised with context below
        raise ConfigError(f"Invalid policies config at {path}: {exc}") from exc


def _read_yaml(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"Config file not found: {p}")
    try:
        with p.open() as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Malformed YAML in {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Config file {p} must contain a YAML mapping at the top level")
    return data


class ConfigError(RuntimeError):
    """Raised when configuration fails validation. Should abort startup."""
