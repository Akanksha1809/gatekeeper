"""Authentication service: resolves a presented API key to a Client+ApiKey row."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gatekeeper.auth.api_keys import parse_api_key, verify_secret
from gatekeeper.db.models import ApiKey, Client
from gatekeeper.errors import ExpiredApiKeyError, InvalidApiKeyError, RevokedApiKeyError


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    client_id: str
    client_name: str
    tier: str
    api_key_id: str


async def authenticate_api_key(session: AsyncSession, raw_key: str) -> AuthenticatedPrincipal:
    """Validate a presented API key end to end. Raises a GatewayError subclass on failure."""
    parsed = parse_api_key(raw_key)
    if parsed is None:
        raise InvalidApiKeyError("API key is malformed")

    row = await session.scalar(select(ApiKey).where(ApiKey.key_id == parsed.key_id))
    if row is None or not verify_secret(parsed.secret, row.secret_hash):
        raise InvalidApiKeyError("API key is invalid")

    now = datetime.now(UTC)
    if row.revoked_at is not None and row.revoked_at <= now:
        raise RevokedApiKeyError("API key has been revoked")
    if row.expires_at is not None and row.expires_at <= now:
        raise ExpiredApiKeyError("API key has expired")

    client = await session.get(Client, row.client_id)
    if client is None:
        raise InvalidApiKeyError("API key is invalid")

    row.last_used_at = now
    await session.commit()

    return AuthenticatedPrincipal(
        client_id=str(client.id),
        client_name=client.name,
        tier=client.tier.value,
        api_key_id=str(row.id),
    )
