"""Unit tests for authenticate_api_key() using a lightweight fake AsyncSession
(no real database) so the authentication decision logic -- malformed/missing/
expired/revoked/invalid key handling -- is tested in isolation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from gatekeeper.auth.api_keys import generate_api_key
from gatekeeper.auth.service import authenticate_api_key
from gatekeeper.errors import ExpiredApiKeyError, InvalidApiKeyError, RevokedApiKeyError


@dataclass
class FakeClient:
    id: uuid.UUID
    name: str = "acme"

    class _Tier:
        value = "standard"

    tier = _Tier()


@dataclass
class FakeApiKeyRow:
    id: uuid.UUID
    client_id: uuid.UUID
    key_id: str
    secret_hash: str
    revoked_at: datetime | None = None
    expires_at: datetime | None = None
    last_used_at: datetime | None = None


@dataclass
class FakeSession:
    api_key_rows: list = field(default_factory=list)
    clients_by_id: dict = field(default_factory=dict)
    committed: bool = False

    async def scalar(self, _stmt):
        # The only query pattern used by authenticate_api_key is "find by key_id".
        # We fake it by stashing the target key_id on the session before calling.
        for row in self.api_key_rows:
            if row.key_id == self._lookup_key_id:
                return row
        return None

    async def get(self, _model, pk):
        return self.clients_by_id.get(pk)

    async def commit(self):
        self.committed = True


def _make_session_with_key(secret_hash, key_id, *, client_id=None, revoked_at=None, expires_at=None):
    client_id = client_id or uuid.uuid4()
    row = FakeApiKeyRow(
        id=uuid.uuid4(),
        client_id=client_id,
        key_id=key_id,
        secret_hash=secret_hash,
        revoked_at=revoked_at,
        expires_at=expires_at,
    )
    session = FakeSession(api_key_rows=[row], clients_by_id={client_id: FakeClient(id=client_id)})
    session._lookup_key_id = key_id
    return session


async def test_valid_key_authenticates_successfully():
    full_key, key_id, secret_hash = generate_api_key()
    session = _make_session_with_key(secret_hash, key_id)

    principal = await authenticate_api_key(session, full_key)

    assert principal.tier == "standard"
    assert principal.client_name == "acme"
    assert session.committed is True


async def test_missing_key_id_is_rejected():
    full_key, key_id, secret_hash = generate_api_key()
    session = _make_session_with_key(secret_hash, "a-different-key-id")
    session._lookup_key_id = key_id  # the DB simply has no matching row

    with pytest.raises(InvalidApiKeyError):
        await authenticate_api_key(session, full_key)


async def test_malformed_key_is_rejected():
    session = FakeSession()
    session._lookup_key_id = "irrelevant"
    with pytest.raises(InvalidApiKeyError):
        await authenticate_api_key(session, "not-a-valid-gatekeeper-key")


async def test_wrong_secret_is_rejected():
    full_key, key_id, secret_hash = generate_api_key()
    session = _make_session_with_key(secret_hash, key_id)

    tampered_key = full_key[:-4] + "xxxx"
    with pytest.raises(InvalidApiKeyError):
        await authenticate_api_key(session, tampered_key)


async def test_revoked_key_is_rejected():
    full_key, key_id, secret_hash = generate_api_key()
    session = _make_session_with_key(secret_hash, key_id, revoked_at=datetime.now(UTC) - timedelta(minutes=1))
    with pytest.raises(RevokedApiKeyError):
        await authenticate_api_key(session, full_key)


async def test_expired_key_is_rejected():
    full_key, key_id, secret_hash = generate_api_key()
    session = _make_session_with_key(secret_hash, key_id, expires_at=datetime.now(UTC) - timedelta(minutes=1))
    with pytest.raises(ExpiredApiKeyError):
        await authenticate_api_key(session, full_key)


async def test_not_yet_expired_key_is_accepted():
    full_key, key_id, secret_hash = generate_api_key()
    session = _make_session_with_key(secret_hash, key_id, expires_at=datetime.now(UTC) + timedelta(days=1))
    principal = await authenticate_api_key(session, full_key)
    assert principal is not None
