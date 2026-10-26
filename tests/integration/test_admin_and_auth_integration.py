"""Integration tests for the admin API-key lifecycle backed by a real
Postgres database (migrated via alembic by scripts/run_integration_tests.sh).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gatekeeper.auth.service import authenticate_api_key

ADMIN_AUTH = ("admin", "change-me-admin-password")


@asynccontextmanager
async def independent_session(database_url: str):
    """A standalone engine/session bound to the *current* event loop, instead
    of reusing gatekeeper.db.session's global engine (which was created
    inside the TestClient's own background event-loop thread and can't be
    touched from the test's event loop -- asyncpg connections are not
    portable across loops).
    """
    engine = create_async_engine(database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        yield session
    await engine.dispose()


def test_create_client_persists_to_postgres(app_client):
    resp = app_client.post(
        "/admin/clients", json={"name": "integration-test-client", "tier": "standard"}, auth=ADMIN_AUTH
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["tier"] == "standard"

    listed = app_client.get("/admin/clients", auth=ADMIN_AUTH).json()
    assert any(c["id"] == body["id"] for c in listed)


def test_create_api_key_is_shown_once_and_only_hash_is_stored(app_client):
    resp = app_client.post(
        "/admin/api-keys",
        json={"client_name": "key-test-client", "tier": "free", "label": "ci"},
        auth=ADMIN_AUTH,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["api_key"].startswith("gk_")

    listed = app_client.get("/admin/api-keys", auth=ADMIN_AUTH).json()
    match = next(k for k in listed if k["key_id"] == body["key_id"])
    assert match["label"] == "ci"
    # the listing endpoint must never expose the secret.
    assert "api_key" not in match
    assert "secret" not in match


async def test_created_key_authenticates_successfully_against_real_db(app_client, database_url):
    resp = app_client.post(
        "/admin/api-keys", json={"client_name": "auth-flow-client", "tier": "premium"}, auth=ADMIN_AUTH
    )
    full_key = resp.json()["api_key"]

    async with independent_session(database_url) as session:
        principal = await authenticate_api_key(session, full_key)
    assert principal.tier == "premium"
    assert principal.client_name == "auth-flow-client"


async def test_revoked_key_fails_authentication(app_client, database_url):
    from gatekeeper.errors import RevokedApiKeyError

    resp = app_client.post(
        "/admin/api-keys", json={"client_name": "revoke-flow-client", "tier": "free"}, auth=ADMIN_AUTH
    )
    body = resp.json()
    full_key, key_id = body["api_key"], body["key_id"]

    revoke_resp = app_client.post(f"/admin/api-keys/{key_id}/revoke", auth=ADMIN_AUTH)
    assert revoke_resp.status_code == 200

    async with independent_session(database_url) as session:
        try:
            await authenticate_api_key(session, full_key)
            raise AssertionError("expected RevokedApiKeyError")
        except RevokedApiKeyError:
            pass


def test_admin_endpoints_reject_missing_credentials(app_client):
    resp = app_client.get("/admin/api-keys")
    assert resp.status_code == 401


def test_admin_endpoints_reject_wrong_credentials(app_client):
    resp = app_client.get("/admin/api-keys", auth=("admin", "wrong-password"))
    assert resp.status_code == 401


def test_proxy_rejects_missing_api_key(app_client):
    resp = app_client.get("/api/users/1")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "MISSING_API_KEY"


def test_readyz_reports_real_dependency_health(app_client):
    resp = app_client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["dependencies"]["redis"] is True
    assert body["dependencies"]["postgres"] is True
