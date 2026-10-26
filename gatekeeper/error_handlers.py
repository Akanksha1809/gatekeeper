"""Centralized exception -> JSON error response mapping.

Everything the gateway itself rejects comes back in the shape:
    {"error": {"code": ..., "message": ..., "request_id": ...}}
Internal/unexpected exceptions are logged with full detail server-side but
returned to the client as a generic 500 with no stack trace or internal
detail, so we never leak backend implementation details to callers.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from gatekeeper.errors import GatewayError, error_body
from gatekeeper.logging_setup import log_with_fields
from gatekeeper.request_context import get_request_id

logger = logging.getLogger("gatekeeper.errors")


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(GatewayError)
    async def handle_gateway_error(request: Request, exc: GatewayError) -> JSONResponse:
        request_id = get_request_id()
        log_with_fields(
            logger,
            logging.WARNING,
            "gateway error",
            code=exc.code,
            status_code=exc.status_code,
            request_id=request_id,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, request_id),
            headers=exc.headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        request_id = get_request_id()
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body("HTTP_ERROR", str(exc.detail), request_id),
            headers=exc.headers or {},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        request_id = get_request_id()
        return JSONResponse(
            status_code=422,
            content=error_body("VALIDATION_ERROR", "Request failed validation", request_id),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = get_request_id()
        logger.exception("unhandled exception", extra={"fields": {"request_id": request_id}})
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "An unexpected error occurred", request_id),
        )
