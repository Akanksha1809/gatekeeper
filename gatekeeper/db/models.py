"""SQLAlchemy models for client/key/policy metadata and audit events.

Plaintext API secrets are never stored -- only a SHA-256 keyed digest
(ApiKey.secret_hash) plus a short, non-secret `key_id` used to look the
row up without scanning the whole table.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Tier(str, enum.Enum):
    free = "free"
    standard = "standard"
    premium = "premium"


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    tier: Mapped[Tier] = mapped_column(Enum(Tier, name="client_tier"), default=Tier.free, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="client", cascade="all, delete-orphan")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)

    # Short, non-secret identifier embedded in the API key's visible prefix.
    # Used to find the candidate row in O(1) before doing the constant-time
    # hash comparison, so we never need to scan/compare against every key.
    key_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    secret_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    client: Mapped[Client] = relationship(back_populates="api_keys")

    __table_args__ = (Index("ix_api_keys_client_id", "client_id"),)


class AuditEventType(str, enum.Enum):
    key_created = "key_created"
    key_revoked = "key_revoked"
    key_auth_failed = "key_auth_failed"


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[AuditEventType] = mapped_column(
        Enum(AuditEventType, name="audit_event_type"), nullable=False
    )
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("clients.id", ondelete="SET NULL"), nullable=True
    )
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
    )
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
