"""initial schema: clients, api_keys, audit_events

Revision ID: 0001_initial
Revises:
Create Date: 2026-01-01 00:00:00

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    client_tier = postgresql.ENUM("free", "standard", "premium", name="client_tier")
    client_tier.create(op.get_bind(), checkfirst=True)
    audit_event_type = postgresql.ENUM(
        "key_created", "key_revoked", "key_auth_failed", name="audit_event_type"
    )
    audit_event_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "clients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("tier", postgresql.ENUM("free", "standard", "premium", name="client_tier", create_type=False), nullable=False, server_default="free"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key_id", sa.String(32), nullable=False, unique=True),
        sa.Column("secret_hash", sa.String(128), nullable=False),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_keys_key_id", "api_keys", ["key_id"], unique=True)
    op.create_index("ix_api_keys_client_id", "api_keys", ["client_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", postgresql.ENUM("key_created", "key_revoked", "key_auth_failed", name="audit_event_type", create_type=False), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id", ondelete="SET NULL"), nullable=True),
        sa.Column("api_key_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_index("ix_api_keys_client_id", table_name="api_keys")
    op.drop_index("ix_api_keys_key_id", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_table("clients")
    postgresql.ENUM(name="audit_event_type").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="client_tier").drop(op.get_bind(), checkfirst=True)
