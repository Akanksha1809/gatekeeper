"""Centralized error types and the machine-readable error-response format.

Every error the gateway itself produces (as opposed to a response faithfully
relayed from an upstream) uses this shape:

    {
        "error": {
            "code": "RATE_LIMIT_EXCEEDED",
            "message": "human readable, safe to show",
            "request_id": "..."
        }
    }

Upstream responses are passed through largely as-is (status code + body),
so a client can tell "the gateway rejected me" apart from "the backend
service itself returned an error".
"""

from __future__ import annotations

from fastapi import status


class GatewayError(Exception):
    """Base class for all errors the gateway generates itself (not relayed upstream errors)."""

    code: str = "GATEWAY_ERROR"
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, message: str, *, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.message = message
        self.headers = headers or {}


class AuthenticationError(GatewayError):
    code = "AUTHENTICATION_FAILED"
    status_code = status.HTTP_401_UNAUTHORIZED


class MissingApiKeyError(AuthenticationError):
    code = "MISSING_API_KEY"


class InvalidApiKeyError(AuthenticationError):
    code = "INVALID_API_KEY"


class ExpiredApiKeyError(AuthenticationError):
    code = "EXPIRED_API_KEY"


class RevokedApiKeyError(AuthenticationError):
    code = "REVOKED_API_KEY"


class AdminAuthenticationError(GatewayError):
    code = "ADMIN_AUTHENTICATION_FAILED"
    status_code = status.HTTP_401_UNAUTHORIZED


class RouteNotFoundError(GatewayError):
    code = "ROUTE_NOT_FOUND"
    status_code = status.HTTP_404_NOT_FOUND


class MethodNotAllowedError(GatewayError):
    code = "METHOD_NOT_ALLOWED"
    status_code = status.HTTP_405_METHOD_NOT_ALLOWED


class RequestTooLargeError(GatewayError):
    code = "REQUEST_TOO_LARGE"
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE


class RateLimitExceededError(GatewayError):
    code = "RATE_LIMIT_EXCEEDED"
    status_code = status.HTTP_429_TOO_MANY_REQUESTS


class CircuitOpenError(GatewayError):
    code = "CIRCUIT_OPEN"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE


class UpstreamTimeoutError(GatewayError):
    code = "UPSTREAM_TIMEOUT"
    status_code = status.HTTP_504_GATEWAY_TIMEOUT


class UpstreamConnectionError(GatewayError):
    code = "UPSTREAM_UNAVAILABLE"
    status_code = status.HTTP_502_BAD_GATEWAY


class DependencyUnavailableError(GatewayError):
    """Redis or Postgres is unreachable and fail-closed policy applies."""

    code = "DEPENDENCY_UNAVAILABLE"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE


class ConfigurationError(GatewayError):
    code = "CONFIGURATION_ERROR"
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR


def error_body(code: str, message: str, request_id: str) -> dict:
    return {"error": {"code": code, "message": message, "request_id": request_id}}
