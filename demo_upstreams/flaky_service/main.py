"""A demo backend that deliberately misbehaves, for exercising retries and
the circuit breaker.

Failure modes (combine via query params):
  - ?fail=true            force this specific request to fail
  - ?status=503           status code to fail with (default 503)
  - every Nth request fails automatically (N = FLAKY_FAILURE_EVERY_N, default 3)

Admin-style controls (not behind gateway auth -- this is an internal test
double, never exposed publicly) to simulate an extended outage:
  - POST /control/unhealthy?seconds=30   fail every request for 30s
  - POST /control/reset                  clear forced-unhealthy state
"""

from __future__ import annotations

import os
import time
from itertools import count

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from demo_upstreams.common import echo_response

app = FastAPI(title="flaky-service")

FAILURE_EVERY_N = int(os.environ.get("FLAKY_FAILURE_EVERY_N", "3"))
_counter = count(1)
_unhealthy_until: float = 0.0


@app.get("/health")
async def health() -> JSONResponse:
    if time.monotonic() < _unhealthy_until:
        return JSONResponse({"status": "unhealthy"}, status_code=503)
    return JSONResponse({"status": "ok"})


@app.post("/control/unhealthy")
async def force_unhealthy(seconds: float = 30.0) -> dict:
    global _unhealthy_until
    _unhealthy_until = time.monotonic() + seconds
    return {"unhealthy_for_seconds": seconds}


@app.post("/control/reset")
async def reset() -> dict:
    global _unhealthy_until
    _unhealthy_until = 0.0
    return {"reset": True}


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def catch_all(full_path: str, request: Request):
    force_fail = request.query_params.get("fail", "").lower() == "true"
    fail_status = int(request.query_params.get("status", "503"))

    if time.monotonic() < _unhealthy_until:
        return JSONResponse({"error": "service unhealthy"}, status_code=503)

    if force_fail:
        return JSONResponse({"error": "forced failure"}, status_code=fail_status)

    n = next(_counter)
    if FAILURE_EVERY_N > 0 and n % FAILURE_EVERY_N == 0:
        return JSONResponse(
            {"error": "periodic simulated failure", "request_number": n}, status_code=fail_status
        )

    return await echo_response(request, "flaky-service", extra={"request_number": n})
