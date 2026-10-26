"""A well-behaved demo backend: responds quickly to anything."""

from __future__ import annotations

from fastapi import FastAPI, Request

from demo_upstreams.common import echo_response

app = FastAPI(title="healthy-service")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def catch_all(full_path: str, request: Request):
    return await echo_response(request, "healthy-service")
