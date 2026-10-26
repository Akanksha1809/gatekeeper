"""A demo backend with a configurable response delay, for exercising gateway
timeouts and retry behavior.

Delay sources, in priority order:
  1. ?delay=<seconds> query parameter on the request itself
  2. SLOW_SERVICE_DEFAULT_DELAY_SECONDS env var
  3. 3.0s default (deliberately longer than the gateway's default route timeout)
"""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, Request

from demo_upstreams.common import echo_response

app = FastAPI(title="slow-service")

DEFAULT_DELAY = float(os.environ.get("SLOW_SERVICE_DEFAULT_DELAY_SECONDS", "3.0"))


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def catch_all(full_path: str, request: Request):
    delay_param = request.query_params.get("delay")
    delay = float(delay_param) if delay_param else DEFAULT_DELAY
    await asyncio.sleep(delay)
    return await echo_response(request, "slow-service", extra={"delay_seconds": delay})
