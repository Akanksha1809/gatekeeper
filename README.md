# Gatekeeper -- API Gateway and Traffic Control Platform

A production-style API gateway: reverse proxying, API-key authentication,
Redis-backed rate limiting (token bucket), timeouts/retries, per-upstream
circuit breakers, request correlation IDs, structured logging, Prometheus
metrics, and a Grafana dashboard -- all runnable locally with one
`docker-compose up`.

See [`docs/architecture.md`](docs/architecture.md) for the design, the
rate-limiting algorithm trade-offs, and known limitations.
See [`docs/testing.md`](docs/testing.md) for how to run the test suite.
See [`docs/runbook.md`](docs/runbook.md) for day-to-day operation and
incident diagnosis.

## Quickstart

```bash
git clone <this repo> && cd RateLimiting
cp .env.example .env            # edit the generated passwords

docker-compose build
docker-compose up -d postgres redis healthy-service slow-service flaky-service
docker-compose run --rm gatekeeper alembic upgrade head
docker-compose up -d gatekeeper nginx prometheus grafana

# bootstrap an API key
curl -s -u admin:$(grep GATEKEEPER_ADMIN_PASSWORD .env | cut -d= -f2) \
  -X POST http://127.0.0.1:8000/admin/api-keys \
  -H "Content-Type: application/json" \
  -d '{"client_name": "demo-client", "tier": "standard"}'

# call it through the gateway
curl -H "X-API-Key: <api_key from above>" http://127.0.0.1:8000/api/users/42
```

| Service | URL |
|---|---|
| Gateway API docs (OpenAPI) | http://127.0.0.1:8000/docs |
| Gateway (direct) | http://127.0.0.1:8000 |
| Gateway (via Nginx edge demo) | http://127.0.0.1:8080 |
| Prometheus | http://127.0.0.1:9090 |
| Grafana | http://127.0.0.1:3000 |

## What's in the box

- **Gateway** (`gatekeeper/`): FastAPI app -- routing, auth, rate
  limiting, retries, circuit breaking, metrics, admin API.
- **Demo upstreams** (`demo_upstreams/`): a healthy echo service, a
  service with a configurable response delay (for timeout testing), and a
  service that deliberately fails on a schedule or on demand (for
  retry/circuit-breaker testing).
- **Config** (`config/routes.yaml`, `config/policies.yaml`): the single
  source of truth for route definitions and rate-limit tiers -- validated
  with Pydantic at startup; a malformed file fails fast with an actionable
  error instead of starting in a half-broken state.
- **Migrations** (`migrations/`): Alembic, against PostgreSQL.
- **Observability** (`monitoring/`): Prometheus scrape config + a
  provisioned Grafana dashboard (throughput, error rate, latency
  percentiles, rate-limit rejections, circuit-breaker state, retries,
  dependency health).
- **Tests** (`tests/`): unit tests (no external deps) and integration
  tests (real Redis + Postgres via `scripts/run_integration_tests.sh`).
- **Load test** (`locustfile.py`): steady / bursty / chaos traffic shapes.
- **CI** (`.github/workflows/ci.yml`): lint, type-check, security scan
  (Bandit + pip-audit + Trivy), unit tests, integration tests, Docker
  builds.

## Local development (without Docker)

```bash
uv venv -p 3.12 .venv
source .venv/bin/activate
uv pip install -p .venv -e ".[dev]"

pytest tests/unit -v
ruff check gatekeeper demo_upstreams tests
mypy gatekeeper
```

Running the gateway itself outside Docker still needs a real Redis and
Postgres reachable at the URLs in your `.env` (or override
`GATEKEEPER_REDIS_URL` / `GATEKEEPER_DATABASE_URL`):

```bash
uvicorn gatekeeper.main:app --reload --port 8000
```

## API-key authentication, in short

- `POST /admin/api-keys` (HTTP Basic, admin-only) issues a key. The full
  secret is returned **exactly once** and only its HMAC-SHA256 digest is
  ever persisted -- the database never holds a recoverable secret.
- Clients send the key as `X-API-Key: gk_<key_id>_<secret>`.
- Keys belong to a client with a tier (`free` / `standard` / `premium`)
  that determines its rate-limit quota, and can be revoked or set to
  expire.
- See [`docs/architecture.md`](docs/architecture.md#rate-limiting-algorithm-and-trade-offs)
  for how tiers map to Redis token buckets.

## License

See [`LICENSE`](LICENSE).
