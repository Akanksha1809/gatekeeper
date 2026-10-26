"""Load test for Gatekeeper.

Usage (against the docker-compose stack):

    source .venv/bin/activate
    locust -f locustfile.py --host http://127.0.0.1:8000

Then open http://localhost:8089 and point it at the gateway. Set an
API_KEY environment variable (create one first via the admin API -- see
docs/testing.md) to exercise the authenticated, rate-limited path; without
one, every request gets a 401 and you're really just load-testing the
auth-rejection path, which is a legitimate (if narrow) thing to measure too.

Three user classes model different traffic shapes:
  - SteadyUser:  requests at a pace below the configured quota -- should
                 see ~0% 429s.
  - BurstyUser:  fires requests back-to-back with no wait -- intended to
                 explore rate-limit rejection behavior and Retry-After.
  - ChaosUser:   hits the slow/flaky demo routes to exercise timeouts,
                 retries, and the circuit breaker under load.
"""

from __future__ import annotations

import os
import random

from locust import HttpUser, between, task

API_KEY = os.environ.get("API_KEY", "")


def _headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY} if API_KEY else {}


class SteadyUser(HttpUser):
    """Normal traffic, well inside quota."""

    wait_time = between(0.5, 1.5)
    weight = 5

    @task
    def get_user(self):
        user_id = random.randint(1, 10_000)
        self.client.get(f"/api/users/{user_id}", headers=_headers(), name="/api/users/[id]")

    @task
    def create_order(self):
        self.client.post(
            "/api/orders/",
            json={"item": "widget", "qty": random.randint(1, 5)},
            headers=_headers(),
            name="/api/orders/",
        )


class BurstyUser(HttpUser):
    """No wait between requests -- intended to trip rate limits."""

    wait_time = between(0, 0)
    weight = 2

    @task
    def hammer_users_endpoint(self):
        with self.client.get(
            "/api/users/1", headers=_headers(), name="/api/users/1 (burst)", catch_response=True
        ) as resp:
            if resp.status_code == 429:
                resp.success()  # a 429 under burst load is the *expected*, correct behavior


class ChaosUser(HttpUser):
    """Exercises the slow/flaky demo backends to stress timeouts, retries,
    and the circuit breaker."""

    wait_time = between(0.2, 1.0)
    weight = 1

    @task(3)
    def call_flaky(self):
        with self.client.get(
            "/api/demo/flaky/ping", headers=_headers(), name="/api/demo/flaky/ping", catch_response=True
        ) as resp:
            if resp.status_code in (502, 503, 504):
                resp.success()  # expected under induced failure / open-circuit conditions

    @task(1)
    def call_slow(self):
        with self.client.get(
            "/api/demo/slow/ping?delay=1.5",
            headers=_headers(),
            name="/api/demo/slow/ping",
            catch_response=True,
        ) as resp:
            if resp.status_code == 504:
                resp.success()  # expected when delay exceeds the route's read timeout
