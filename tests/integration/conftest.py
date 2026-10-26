"""Fixtures for the integration suite. These tests need real Redis and
Postgres instances (see scripts/run_integration_tests.sh) and are marked
`integration` so they're easy to exclude from a quick unit-test run.
"""

from __future__ import annotations

import os

import pytest
import redis.asyncio as redis_asyncio
from fastapi.testclient import TestClient


def pytest_collection_modifyitems(items):
    """Auto-apply the `integration` marker to everything under tests/integration/
    so `pytest -m integration` / `-m "not integration"` work from the repo root.
    """
    for item in items:
        if "tests/integration" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.integration)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set -- run via scripts/run_integration_tests.sh")
    return value


@pytest.fixture
def redis_url() -> str:
    return _require_env("GATEKEEPER_REDIS_URL")


@pytest.fixture
def database_url() -> str:
    return _require_env("GATEKEEPER_DATABASE_URL")


@pytest.fixture
async def redis_client(redis_url):
    client = redis_asyncio.from_url(redis_url)
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
def app_client(database_url, redis_url):
    from gatekeeper.main import create_app
    from gatekeeper.settings import reset_settings_cache

    reset_settings_cache()
    with TestClient(create_app()) as client:
        yield client
