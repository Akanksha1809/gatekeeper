"""The actual HTTP call to an upstream service, via a shared httpx.AsyncClient.

Responsibilities kept narrow and explicit:
  - strip hop-by-hop headers in both directions (RFC 9110 7.6.1)
  - never forward the client's credentials (Authorization / X-API-Key) to
    the backend -- backends don't need the gateway's auth material
  - enforce a response-size ceiling while reading the body
  - translate httpx exceptions into our own typed errors so callers
    (the retry wrapper, the router) don't need to know about httpx

Streaming responses are NOT supported: the full upstream body is buffered
(up to `max_response_body_bytes`) before being returned to the client. This
keeps size-limit enforcement and retry logic simple, at the cost of not
being suitable for very large downloads or server-sent-event / chunked
streaming use cases -- see docs/architecture.md "Known limitations".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from gatekeeper.errors import RequestTooLargeError, UpstreamConnectionError, UpstreamTimeoutError
from gatekeeper.logging_setup import log_with_fields
from gatekeeper.proxy.retry import RetryableError
from gatekeeper.routing_config import TimeoutPolicy

logger = logging.getLogger("gatekeeper.upstream")

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",  # httpx recomputes this for us
}

# Never forwarded to the backend: these carry the caller's credentials,
# which the upstream has no business seeing.
CLIENT_CREDENTIAL_HEADERS = {"authorization", "x-api-key"}

# Never relayed back to the external client: internal/hop-by-hop or
# information that could leak backend implementation details.
RESPONSE_STRIP_HEADERS = HOP_BY_HOP_HEADERS | {"server", "date"}


@dataclass(frozen=True)
class UpstreamResponse:
    status_code: int
    headers: list[tuple[str, str]]
    body: bytes
    elapsed_seconds: float


def filter_request_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        k: v
        for k, v in headers.items()
        if k.lower() not in HOP_BY_HOP_HEADERS and k.lower() not in CLIENT_CREDENTIAL_HEADERS
    }


def filter_response_headers(headers: httpx.Headers) -> list[tuple[str, str]]:
    return [(k, v) for k, v in headers.items() if k.lower() not in RESPONSE_STRIP_HEADERS]


class UpstreamClient:
    def __init__(self, client: httpx.AsyncClient, max_response_body_bytes: int):
        self._client = client
        self._max_response_body_bytes = max_response_body_bytes

    async def send(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str],
        params: list[tuple[str, str]],
        body: bytes,
        timeout: TimeoutPolicy,
        request_id: str,
    ) -> UpstreamResponse:
        httpx_timeout = httpx.Timeout(
            connect=timeout.connect, read=timeout.read, write=timeout.write, pool=timeout.pool
        )
        outgoing_headers = filter_request_headers(headers)
        outgoing_headers["X-Request-ID"] = request_id

        try:
            async with self._client.stream(
                method=method,
                url=url,
                headers=outgoing_headers,
                params=httpx.QueryParams(params),  # type: ignore[arg-type]  # list is invariant; str,str tuples are fine
                content=body,
                timeout=httpx_timeout,
            ) as response:
                chunks = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > self._max_response_body_bytes:
                        raise RequestTooLargeError("Upstream response exceeded the maximum allowed size")
                    chunks.append(chunk)
                response_body = b"".join(chunks)
                return UpstreamResponse(
                    status_code=response.status_code,
                    headers=filter_response_headers(response.headers),
                    body=response_body,
                    elapsed_seconds=response.elapsed.total_seconds(),
                )
        except httpx.TimeoutException as exc:
            # Client-facing message (set on the UpstreamTimeoutError below) is
            # deliberately generic -- it must not leak the backend's address
            # or other internal detail. The specific httpx exception is only
            # logged server-side.
            log_with_fields(
                logger, logging.WARNING, "upstream timeout", request_id=request_id, detail=str(exc)
            )
            raise RetryableError("Upstream request timed out") from UpstreamTimeoutError(
                "Upstream service did not respond in time"
            )
        except httpx.ConnectError as exc:
            log_with_fields(
                logger, logging.WARNING, "upstream connect error", request_id=request_id, detail=str(exc)
            )
            raise RetryableError("Could not connect to upstream") from UpstreamConnectionError(
                "Upstream service is unreachable"
            )
        except httpx.RequestError as exc:
            log_with_fields(
                logger, logging.WARNING, "upstream request error", request_id=request_id, detail=str(exc)
            )
            raise RetryableError("Upstream request failed") from UpstreamConnectionError(
                "Upstream service request failed"
            )
