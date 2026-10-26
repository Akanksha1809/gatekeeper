"""Liveness and readiness probes, plus non-sensitive diagnostics.

Liveness answers "is the process alive and able to serve at all" -- it must
NOT fail just because a dependency is temporarily down, or an orchestrator
will restart the pod and make a transient outage worse.

Readiness answers "can this instance currently serve real traffic" -- it
checks Redis and Postgres connectivity and is what should gate load
balancer traffic / rolling deploys.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from gatekeeper.app_state import AppState, get_app_state
from gatekeeper.auth.admin import require_admin
from gatekeeper.db.session import check_connectivity as db_check
from gatekeeper.rate_limit.limiter import check_connectivity as redis_check
from gatekeeper.schemas import (
    DiagnosticsResponse,
    HealthResponse,
    ReadinessResponse,
    RouteDiagnostic,
)

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    return HealthResponse(status="alive")


@router.get("/readyz", response_model=ReadinessResponse)
async def readiness(state: AppState = Depends(get_app_state)) -> ReadinessResponse:
    redis_ok = await redis_check(state.redis_client)
    postgres_ok = await db_check()
    deps = {"redis": redis_ok, "postgres": postgres_ok}

    from gatekeeper.metrics import DEPENDENCY_UP

    for name, ok in deps.items():
        DEPENDENCY_UP.labels(dependency=name).set(1 if ok else 0)

    overall = "ready" if all(deps.values()) else "not_ready"
    response = ReadinessResponse(status=overall, dependencies=deps)
    from fastapi.responses import JSONResponse

    status_code = 200 if overall == "ready" else 503
    return JSONResponse(status_code=status_code, content=response.model_dump())  # type: ignore[return-value]


@router.get("/admin/diagnostics", response_model=DiagnosticsResponse)
async def diagnostics(
    request: Request,
    state: AppState = Depends(get_app_state),
    _admin: str = Depends(require_admin),
) -> DiagnosticsResponse:
    routes = []
    for route in state.route_table.routes:
        breaker = state.circuit_registry.get_or_create(
            route.name,
            route.circuit_breaker.failure_threshold,
            route.circuit_breaker.cooldown_seconds,
            route.circuit_breaker.half_open_max_calls,
        )
        routes.append(
            RouteDiagnostic(
                name=route.name,
                prefix=route.prefix,
                methods=route.methods,
                rate_limit_scope=route.rate_limit_scope,
                circuit_breaker_state=breaker.state.value,
            )
        )
    return DiagnosticsResponse(routes=routes, environment=state.settings.env)
