"""Request-scoped middleware: correlation ID, structured access log, and
the in-flight-requests gauge.
"""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from gatekeeper import metrics
from gatekeeper.logging_setup import log_with_fields
from gatekeeper.request_context import REQUEST_ID_HEADER, normalize_request_id, set_request_id

access_logger = logging.getLogger("gatekeeper.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        request_id = normalize_request_id(request.headers.get(REQUEST_ID_HEADER))
        set_request_id(request_id)

        route_label = _route_template(request)
        metrics.IN_FLIGHT_REQUESTS.inc()
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            duration = time.monotonic() - start
            metrics.REQUEST_LATENCY.labels(method=request.method, route=route_label).observe(duration)
            metrics.REQUEST_COUNT.labels(method=request.method, route=route_label, status_class="5xx").inc()
            metrics.IN_FLIGHT_REQUESTS.dec()
            log_with_fields(
                access_logger,
                logging.ERROR,
                "request failed",
                method=request.method,
                path=request.url.path,
                request_id=request_id,
                duration_ms=round(duration * 1000, 2),
            )
            raise

        duration = time.monotonic() - start
        response.headers[REQUEST_ID_HEADER] = request_id
        metrics.REQUEST_LATENCY.labels(method=request.method, route=route_label).observe(duration)
        metrics.REQUEST_COUNT.labels(
            method=request.method, route=route_label, status_class=metrics.status_class(response.status_code)
        ).inc()
        metrics.IN_FLIGHT_REQUESTS.dec()

        log_with_fields(
            access_logger,
            logging.INFO,
            "request completed",
            method=request.method,
            path=request.url.path,
            route=route_label,
            status_code=response.status_code,
            request_id=request_id,
            duration_ms=round(duration * 1000, 2),
        )
        return response


def _route_template(request: Request) -> str:
    """The matched route's path *template* (e.g. "/api/{full_path:path}"),
    not the raw URL, so this stays bounded-cardinality when used as a
    metric label. BaseHTTPMiddleware runs before Starlette's router
    annotates `scope["route"]`, so we resolve the match ourselves by asking
    each registered route whether it matches this request's scope.
    """
    from starlette.routing import Match

    for route in request.app.routes:
        match, _ = route.matches(request.scope)
        if match == Match.FULL:
            return getattr(route, "path", "unmatched")
    return "unmatched"
