# Testing Guide

## Unit tests

No external services required.

```bash
source .venv/bin/activate
pytest tests/unit -v --cov=gatekeeper --cov-report=term-missing
```

Covers: API-key generation/parsing/hashing, routing config validation and
longest-prefix resolution, the circuit breaker state machine (via a fake,
manually-advanced clock -- no real sleeps), retry policy and backoff bounds,
the rate limiter's Python-side logic (fail-open/fail-closed, result
construction) against a fake Redis script, the authentication service
against a fake DB session, admin HTTP Basic auth, request-ID
normalization/anti-injection, upstream header filtering, and the
`GatewayService` orchestration (route resolution, rate-limit enforcement,
retry + circuit-breaker interaction) against fakes for the rate limiter and
upstream HTTP client.

## Integration tests

Need real Redis and Postgres. `scripts/run_integration_tests.sh` spins up
disposable instances (via `docker-compose.test.yml`, in an isolated
`gatekeeper-test` Compose project so it won't collide with a demo stack you
already have running), runs migrations, runs the suite, and tears
everything down:

```bash
./scripts/run_integration_tests.sh          # run everything
./scripts/run_integration_tests.sh -k redis # pass extra pytest args through
```

Covers (against the **real** Lua script, not a mock -- this is what
actually proves atomicity):
- normal traffic, quota exhaustion, burst allowance, window rollover/refill
  over real time
- **concurrent requests never exceed the configured quota** (100 concurrent
  coroutines against a 20-token bucket: exactly 20 are admitted)
- separate API keys and separate routes have independent quotas
- fail-closed raises `DependencyUnavailableError` / fail-open admits
  traffic with `degraded=True` when Redis is unreachable
- idle bucket keys expire (bounded Redis memory)
- API-key lifecycle against real Postgres: create, list (secret never
  exposed), authenticate, revoke, expire
- admin endpoints reject missing/wrong credentials
- the gateway rejects requests with no API key
- `/readyz` reflects real dependency health

### Why two test tiers instead of one

The proxy/retry/circuit-breaker path itself is exercised two ways:
unit tests fake the upstream HTTP client and rate limiter to test
*orchestration logic* in isolation (fast, deterministic, no I/O), while the
full request lifecycle -- gateway → real Redis/Postgres → real demo
upstream containers -- is exercised manually via the running
docker-compose stack (see the walkthroughs in `docs/runbook.md`). A
pytest-level "true end-to-end" test would need the gateway's own container
to resolve `healthy-service`/`slow-service`/`flaky-service` by Docker DNS,
which only works from inside the Compose network, not from the host
process running pytest.

## Linting, typing, security

```bash
ruff check gatekeeper demo_upstreams tests
ruff format --check gatekeeper demo_upstreams tests
mypy gatekeeper
bandit -r gatekeeper -ll
pip-audit
```

## Load testing

```bash
source .venv/bin/activate
API_KEY=<a key from the admin API> locust -f locustfile.py --host http://127.0.0.1:8000
```

Open http://localhost:8089. Three user classes model steady traffic inside
quota, bursty traffic intended to trigger 429s, and "chaos" traffic against
the slow/flaky demo backends to exercise timeouts, retries, and the circuit
breaker under load.
