"""Shared helpers for the demo upstream services."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


async def echo_response(request: Request, service_name: str, extra: dict | None = None) -> JSONResponse:
    body = await request.body()
    payload = {
        "service": service_name,
        "method": request.method,
        "path": request.url.path,
        "query": dict(request.query_params),
        "body_length": len(body),
        "request_id": request.headers.get("x-request-id"),
    }
    if extra:
        payload.update(extra)
    return JSONResponse(payload)
