"""Admin-only API-key and client management endpoints.

Everything under /admin requires HTTP Basic auth against
GATEKEEPER_ADMIN_USERNAME / GATEKEEPER_ADMIN_PASSWORD (see auth/admin.py).
There is no unauthenticated key-management operation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gatekeeper.auth.admin import require_admin
from gatekeeper.auth.api_keys import generate_api_key
from gatekeeper.db.models import ApiKey, AuditEvent, AuditEventType, Client, Tier
from gatekeeper.db.session import session_dependency
from gatekeeper.schemas import (
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    ApiKeyListItem,
    ClientCreateRequest,
    ClientResponse,
    RevokeResponse,
)
from gatekeeper.settings import Settings, get_settings

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.post("/clients", response_model=ClientResponse, status_code=201)
async def create_client(
    body: ClientCreateRequest,
    session: AsyncSession = Depends(session_dependency),
) -> ClientResponse:
    client = Client(name=body.name, tier=Tier(body.tier))
    session.add(client)
    await session.commit()
    return ClientResponse(
        id=str(client.id), name=client.name, tier=client.tier.value, created_at=client.created_at
    )


@router.get("/clients", response_model=list[ClientResponse])
async def list_clients(session: AsyncSession = Depends(session_dependency)) -> list[ClientResponse]:
    rows = (await session.scalars(select(Client).order_by(Client.created_at.desc()))).all()
    return [
        ClientResponse(id=str(c.id), name=c.name, tier=c.tier.value, created_at=c.created_at) for c in rows
    ]


@router.post("/api-keys", response_model=ApiKeyCreateResponse, status_code=201)
async def create_api_key(
    body: ApiKeyCreateRequest,
    session: AsyncSession = Depends(session_dependency),
    settings: Settings = Depends(get_settings),
) -> ApiKeyCreateResponse:
    client: Client | None = None
    if body.client_id:
        try:
            client_uuid = uuid.UUID(body.client_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="client_id must be a valid UUID") from None
        client = await session.get(Client, client_uuid)
        if client is None:
            raise HTTPException(status_code=404, detail="client not found")
    elif body.client_name:
        client = Client(name=body.client_name, tier=Tier(body.tier))
        session.add(client)
        await session.flush()
    else:
        raise HTTPException(status_code=400, detail="either client_id or client_name is required")

    full_key, key_id, secret_hash = generate_api_key(prefix=settings.api_key_prefix)
    expires_at = datetime.now(UTC) + timedelta(days=body.expires_in_days) if body.expires_in_days else None
    api_key = ApiKey(
        client_id=client.id,
        key_id=key_id,
        secret_hash=secret_hash,
        label=body.label,
        expires_at=expires_at,
    )
    session.add(api_key)
    await session.flush()
    session.add(
        AuditEvent(
            event_type=AuditEventType.key_created,
            client_id=client.id,
            api_key_id=api_key.id,
            detail=f"key_id={key_id}",
        )
    )
    await session.commit()

    return ApiKeyCreateResponse(
        api_key=full_key,
        key_id=key_id,
        client_id=str(client.id),
        tier=client.tier.value,
        expires_at=expires_at,
    )


@router.get("/api-keys", response_model=list[ApiKeyListItem])
async def list_api_keys(session: AsyncSession = Depends(session_dependency)) -> list[ApiKeyListItem]:
    rows = (await session.scalars(select(ApiKey).order_by(ApiKey.created_at.desc()))).all()
    result = []
    for row in rows:
        client = await session.get(Client, row.client_id)
        result.append(
            ApiKeyListItem(
                key_id=row.key_id,
                client_id=str(row.client_id),
                client_name=client.name if client else "<deleted>",
                tier=client.tier.value if client else "unknown",
                label=row.label,
                created_at=row.created_at,
                expires_at=row.expires_at,
                revoked_at=row.revoked_at,
                last_used_at=row.last_used_at,
            )
        )
    return result


@router.post("/api-keys/{key_id}/revoke", response_model=RevokeResponse)
async def revoke_api_key(
    key_id: str,
    session: AsyncSession = Depends(session_dependency),
) -> RevokeResponse:
    row = await session.scalar(select(ApiKey).where(ApiKey.key_id == key_id))
    if row is None:
        raise HTTPException(status_code=404, detail="API key not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        session.add(
            AuditEvent(
                event_type=AuditEventType.key_revoked,
                client_id=row.client_id,
                api_key_id=row.id,
                detail=f"key_id={key_id}",
            )
        )
        await session.commit()
    return RevokeResponse(key_id=key_id, revoked=True)
