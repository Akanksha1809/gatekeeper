"""Typed accessors for the shared resources stashed on `app.state` during
the FastAPI lifespan (engine, redis client, gateway service, etc). Centralizing
these as small functions avoids scattering `request.app.state.foo` string
lookups throughout the routers.
"""

from __future__ import annotations

from dataclasses import dataclass

import redis.asyncio as redis_asyncio
from fastapi import Request

from gatekeeper.proxy.circuit_breaker import CircuitBreakerRegistry
from gatekeeper.proxy.router import GatewayService
from gatekeeper.routing_config import PoliciesFile, RouteTable
from gatekeeper.settings import Settings


@dataclass
class AppState:
    settings: Settings
    route_table: RouteTable
    policies: PoliciesFile
    redis_client: redis_asyncio.Redis
    gateway_service: GatewayService
    circuit_registry: CircuitBreakerRegistry
    ready: bool = False


def get_app_state(request: Request) -> AppState:
    return request.app.state.gatekeeper
