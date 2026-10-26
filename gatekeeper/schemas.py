"""Pydantic request/response schemas for the admin and health APIs."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ClientCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    tier: str = Field(default="free", pattern="^(free|standard|premium)$")


class ClientResponse(BaseModel):
    id: str
    name: str
    tier: str
    created_at: datetime


class ApiKeyCreateRequest(BaseModel):
    client_id: str | None = Field(default=None, description="Existing client ID to attach the key to")
    client_name: str | None = Field(
        default=None, description="If client_id is omitted, create a new client with this name"
    )
    tier: str = Field(default="free", pattern="^(free|standard|premium)$")
    label: str | None = Field(default=None, max_length=255)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiKeyCreateResponse(BaseModel):
    api_key: str = Field(description="The full secret API key. Shown only once -- store it securely.")
    key_id: str
    client_id: str
    tier: str
    expires_at: datetime | None


class ApiKeyListItem(BaseModel):
    key_id: str
    client_id: str
    client_name: str
    tier: str
    label: str | None
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None


class RevokeResponse(BaseModel):
    key_id: str
    revoked: bool


class HealthResponse(BaseModel):
    status: str


class ReadinessResponse(BaseModel):
    status: str
    dependencies: dict[str, bool]


class RouteDiagnostic(BaseModel):
    name: str
    prefix: str
    methods: list[str]
    rate_limit_scope: str
    circuit_breaker_state: str


class DiagnosticsResponse(BaseModel):
    routes: list[RouteDiagnostic]
    environment: str
