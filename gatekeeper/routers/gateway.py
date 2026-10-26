"""The client-facing catch-all proxy endpoint.

Mounted at the very end of the router list so health/metrics/admin routes
are matched first; anything left over here is checked against the
configured route table (see routing_config.py) and proxied, or rejected
with a typed error if no route/method matches, the API key is bad, the
caller is rate limited, or the upstream's circuit breaker is open.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from gatekeeper import metrics
from gatekeeper.app_state import AppState, get_app_state
from gatekeeper.auth.service import AuthenticatedPrincipal, authenticate_api_key
from gatekeeper.db.session import session_dependency
from gatekeeper.errors import MissingApiKeyError, RequestTooLargeError
from gatekeeper.proxy.router import ProxyRequest
from gatekeeper.request_context import get_request_id

router = APIRouter(tags=["gateway"])

_API_KEY_HEADER = "x-api-key"


async def _read_limited_body(request: Request, max_bytes: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > max_bytes:
                raise RequestTooLargeError("Request body exceeds the maximum allowed size")
        except ValueError:
            pass  # malformed header; fall through to streamed enforcement below

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise RequestTooLargeError("Request body exceeds the maximum allowed size")
        chunks.append(chunk)
    return b"".join(chunks)


async def require_principal(
    request: Request,
    state: AppState = Depends(get_app_state),
    session: AsyncSession = Depends(session_dependency),
) -> AuthenticatedPrincipal:
    raw_key = request.headers.get(_API_KEY_HEADER)
    if not raw_key:
        metrics.AUTH_FAILURES.labels(reason="missing_key").inc()
        raise MissingApiKeyError("Missing API key. Provide it in the 'X-API-Key' header.")
    try:
        return await authenticate_api_key(session, raw_key)
    except Exception as exc:
        reason = getattr(exc, "code", "invalid_key").lower()
        metrics.AUTH_FAILURES.labels(reason=reason).inc()
        raise


@router.api_route(
    "/api/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def proxy(
    full_path: str,
    request: Request,
    state: AppState = Depends(get_app_state),
    principal: AuthenticatedPrincipal = Depends(require_principal),
) -> Response:
    path = "/api/" + full_path
    route, remainder = state.gateway_service.resolve_route(path, request.method)

    rate_limit_headers = await state.gateway_service.enforce_rate_limits(route, principal)

    body = await _read_limited_body(request, state.settings.max_request_body_bytes)

    proxy_request = ProxyRequest(
        method=request.method,
        path=path,
        headers=dict(request.headers),
        query_params=list(request.query_params.multi_items()),
        body=body,
        request_id=get_request_id(),
    )

    upstream_response = await state.gateway_service.forward(route, remainder, proxy_request)

    response_headers = dict(upstream_response.headers)
    response_headers["X-RateLimit-Limit"] = str(rate_limit_headers.limit)
    response_headers["X-RateLimit-Remaining"] = str(rate_limit_headers.remaining)
    response_headers["X-Request-ID"] = get_request_id()

    return Response(
        content=upstream_response.body,
        status_code=upstream_response.status_code,
        headers=response_headers,
    )


@router.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
async def unmatched(full_path: str, request: Request) -> Response:
    """Catches anything that isn't /healthz, /readyz, /metrics, /admin/*, or /api/*."""
    from gatekeeper.errors import RouteNotFoundError

    raise RouteNotFoundError(f"No route configured for path {'/' + full_path!r}")
