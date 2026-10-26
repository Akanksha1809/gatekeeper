"""FastAPI application factory and lifespan wiring."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as redis_asyncio
from fastapi import FastAPI

from gatekeeper.app_state import AppState
from gatekeeper.db.session import dispose_engine, init_engine
from gatekeeper.error_handlers import register_error_handlers
from gatekeeper.logging_setup import configure_logging, log_with_fields
from gatekeeper.metrics import CIRCUIT_BREAKER_TRANSITIONS, set_circuit_state_metric
from gatekeeper.middleware import RequestContextMiddleware
from gatekeeper.proxy.circuit_breaker import CircuitBreakerRegistry
from gatekeeper.proxy.router import GatewayService
from gatekeeper.proxy.upstream import UpstreamClient
from gatekeeper.rate_limit.limiter import RateLimiter
from gatekeeper.routers import admin, gateway, health
from gatekeeper.routers import metrics_router as metrics_router_module
from gatekeeper.routing_config import ConfigError, load_policies, load_routes
from gatekeeper.settings import get_settings

logger = logging.getLogger("gatekeeper.main")


def _on_breaker_transition(route_name: str, old_state, new_state) -> None:
    set_circuit_state_metric(route_name, new_state.value)
    CIRCUIT_BREAKER_TRANSITIONS.labels(route=route_name, to_state=new_state.value).inc()
    log_with_fields(
        logger,
        logging.WARNING,
        "circuit breaker transition",
        route=route_name,
        from_state=old_state.value,
        to_state=new_state.value,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    try:
        route_table = load_routes(settings.routes_config)
        policies = load_policies(settings.policies_config)
    except ConfigError as exc:
        logger.error("Fatal configuration error at startup: %s", exc)
        raise

    redis_client = redis_asyncio.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.redis_connect_timeout_seconds,
        socket_timeout=settings.redis_command_timeout_seconds,
        decode_responses=False,
    )
    rate_limiter = RateLimiter(redis_client, fail_mode=settings.rate_limit_fail_mode)

    init_engine(settings)

    httpx_client = httpx.AsyncClient()
    upstream_client = UpstreamClient(httpx_client, settings.max_response_body_bytes)

    circuit_registry = CircuitBreakerRegistry(on_transition=_on_breaker_transition)

    gateway_service = GatewayService(
        route_table=route_table,
        policies=policies,
        rate_limiter=rate_limiter,
        circuit_registry=circuit_registry,
        upstream_client=upstream_client,
    )

    app.state.gatekeeper = AppState(
        settings=settings,
        route_table=route_table,
        policies=policies,
        redis_client=redis_client,
        gateway_service=gateway_service,
        circuit_registry=circuit_registry,
        ready=True,
    )

    log_with_fields(
        logger, logging.INFO, "gatekeeper started", env=settings.env, routes=len(route_table.routes)
    )

    try:
        yield
    finally:
        log_with_fields(logger, logging.INFO, "gatekeeper shutting down")
        await httpx_client.aclose()
        await redis_client.aclose()  # type: ignore[attr-defined]  # present at runtime; stub lags redis-py 5.x
        await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Gatekeeper",
        description="API Gateway and Traffic Control Platform",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)

    app.include_router(health.router)
    app.include_router(metrics_router_module.router)
    app.include_router(admin.router)
    app.include_router(gateway.router)

    return app


app = create_app()
