#!/usr/bin/env bash
# Spins up Postgres + Redis (+ demo upstreams) with host ports exposed via
# docker-compose.test.yml, runs migrations, runs the integration test
# suite against them, then tears everything down. See docs/testing.md.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

export TEST_POSTGRES_PORT="${TEST_POSTGRES_PORT:-55432}"
export TEST_REDIS_PORT="${TEST_REDIS_PORT:-56379}"

# A distinct project name keeps this from colliding with a docker-compose
# stack you might already have up for manual testing/demoing.
COMPOSE="docker-compose -p gatekeeper-test -f docker-compose.yml -f docker-compose.test.yml"

cleanup() {
  echo "--- tearing down test infrastructure ---"
  $COMPOSE down -v
}
trap cleanup EXIT

echo "--- starting postgres, redis, demo upstreams ---"
$COMPOSE up -d postgres redis healthy-service slow-service flaky-service

echo "--- waiting for health ---"
for i in $(seq 1 30); do
  if $COMPOSE ps postgres redis | grep -c "healthy" | grep -q 2; then
    break
  fi
  sleep 1
done

export GATEKEEPER_DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER:-gatekeeper}:${POSTGRES_PASSWORD:-change-me-postgres-password}@localhost:${TEST_POSTGRES_PORT}/${POSTGRES_DB:-gatekeeper}"
export GATEKEEPER_REDIS_URL="redis://localhost:${TEST_REDIS_PORT}/0"
# Override the container-path defaults picked up from .env so the app can
# find the config files when run directly on the host (not in Docker).
export GATEKEEPER_ROUTES_CONFIG="$(pwd)/config/routes.yaml"
export GATEKEEPER_POLICIES_CONFIG="$(pwd)/config/policies.yaml"

echo "--- running alembic migrations against localhost:${TEST_POSTGRES_PORT} ---"
source .venv/bin/activate
GATEKEEPER_DATABASE_URL="$GATEKEEPER_DATABASE_URL" alembic upgrade head

echo "--- running integration tests ---"
GATEKEEPER_DATABASE_URL="$GATEKEEPER_DATABASE_URL" \
  GATEKEEPER_REDIS_URL="$GATEKEEPER_REDIS_URL" \
  pytest tests/integration -v "$@"
