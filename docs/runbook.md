# Operational Runbook

## Starting and stopping the stack

```bash
cp .env.example .env            # edit passwords before anything but local demo use
docker-compose build
docker-compose up -d postgres redis healthy-service slow-service flaky-service
docker-compose run --rm gatekeeper alembic upgrade head
docker-compose up -d gatekeeper nginx prometheus grafana
```

Stop everything (keeps data volumes):
```bash
docker-compose stop
```

Stop and remove containers (keeps data volumes):
```bash
docker-compose down
```

Full reset, including data (**destructive**):
```bash
docker-compose down -v
```

## Where things live

| What | URL |
|---|---|
| Gateway (direct) | http://127.0.0.1:8000 |
| Gateway (via Nginx edge demo) | http://127.0.0.1:8080 |
| Prometheus | http://127.0.0.1:9090 |
| Grafana (admin / `$GRAFANA_ADMIN_PASSWORD`) | http://127.0.0.1:3000 |

Postgres and Redis are **not** published to the host -- only the gateway
container can reach them, by Docker DNS name, over the `data` network
(`internal: true` in `docker-compose.yml`). This is intentional: nothing
but the gateway should ever talk to them directly in anything beyond a
local demo.

## Bootstrapping a test API key

```bash
curl -s -u admin:$GATEKEEPER_ADMIN_PASSWORD -X POST http://127.0.0.1:8000/admin/api-keys \
  -H "Content-Type: application/json" \
  -d '{"client_name": "my-test-client", "tier": "standard"}'
```

The response's `api_key` field is shown **exactly once** -- store it now.
Only its hash is ever persisted.

```bash
curl -H "X-API-Key: <api_key from above>" http://127.0.0.1:8000/api/users/42
```

## Diagnosing a production-style incident

1. **Check readiness first**: `curl http://127.0.0.1:8000/readyz`. If
   `redis` or `postgres` is `false`, that's your root cause -- check the
   dependency's own container logs/health (`docker-compose ps`,
   `docker-compose logs postgres`).
2. **Check circuit breaker state** (no secrets exposed):
   ```bash
   curl -u admin:$GATEKEEPER_ADMIN_PASSWORD http://127.0.0.1:8000/admin/diagnostics
   ```
   A route stuck `open` means its upstream has been failing past
   `failure_threshold`; it will self-probe again after `cooldown_seconds`.
3. **Check the Grafana dashboard** ("Gatekeeper Overview") for error rate,
   latency percentiles, rate-limit rejections, and retry volume around the
   incident window.
4. **Correlate a specific failing request** by its `X-Request-ID` response
   header, then grep the gateway's JSON logs for that ID:
   ```bash
   docker-compose logs gatekeeper | grep '"request_id": "<id>"'
   ```
5. **Rate-limit false positives**: check `/metrics` for
   `gatekeeper_rate_limit_rejections_total` by route/tier, and confirm the
   client's tier/policy in `config/policies.yaml` matches what you expect
   -- a client created without specifying a tier defaults to `free`.

## Simulating failure scenarios (using the demo backends)

```bash
# Force the flaky-service unhealthy for 30s (affects /api/demo/flaky/*)
docker-compose exec flaky-service python -c "
import httpx
httpx.post('http://localhost:9003/control/unhealthy', params={'seconds': 30})
"

# Or, from the gateway's perspective, force individual failures:
curl -H "X-API-Key: $API_KEY" "http://127.0.0.1:8000/api/demo/flaky/ping?fail=true&status=503"

# Exercise the timeout path (route read-timeout is 2s, backend delay 3s by default):
curl -H "X-API-Key: $API_KEY" http://127.0.0.1:8000/api/demo/slow/ping
```

Three or more consecutive failures on `demo-flaky` (see
`config/routes.yaml`'s `failure_threshold`) will trip its circuit breaker;
subsequent requests get an immediate `503 CIRCUIT_OPEN` without hitting the
backend at all until the cooldown elapses and a half-open probe succeeds.

## Database migrations

```bash
# Apply all pending migrations
docker-compose run --rm gatekeeper alembic upgrade head

# Create a new migration after changing gatekeeper/db/models.py
source .venv/bin/activate
GATEKEEPER_DATABASE_URL=postgresql+asyncpg://... alembic revision --autogenerate -m "describe the change"

# Roll back one migration
docker-compose run --rm gatekeeper alembic downgrade -1
```

## Restart recovery

Every service has `restart: unless-stopped` and a real healthcheck.
Restarting any single dependency (`docker-compose restart redis`) should
not require restarting the gateway: the gateway tolerates a transient
dependency outage per `GATEKEEPER_RATE_LIMIT_FAIL_MODE` for Redis, and
SQLAlchemy's `pool_pre_ping=True` recycles stale Postgres connections
automatically.
