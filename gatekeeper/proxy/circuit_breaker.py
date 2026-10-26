"""Per-upstream, in-process circuit breaker.

States
------
CLOSED    - normal operation. Consecutive failures are counted; hitting
            `failure_threshold` trips the breaker to OPEN.
OPEN      - all calls are rejected immediately (no network call made) until
            `cooldown_seconds` has elapsed, at which point the breaker moves
            to HALF_OPEN.
HALF_OPEN - a limited number of probe calls (`half_open_max_calls`) are let
            through. Any failure among them reopens the breaker immediately
            and restarts the cooldown. If all probes succeed, the breaker
            closes and the failure counter resets.

This breaker is intentionally in-process / per-gateway-instance: each
replica tracks its own upstream health independently. That means a fleet of
N replicas needs roughly N times the failures to all trip open compared to
a single instance. Coordinating breaker state across replicas would need a
shared store (e.g. Redis) with its own consistency/latency trade-offs; we
don't add that here because a single slow-but-correct breaker per process
is sufficient for this project's scope and keeps the breaker decision on
the hot path free of an extra network round-trip. See docs/architecture.md.

The clock is injectable so tests can simulate cooldowns deterministically
instead of sleeping.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenException(Exception):
    pass


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int,
        cooldown_seconds: float,
        half_open_max_calls: int,
        clock: Callable[[], float] | None = None,
        on_transition: Callable[[str, CircuitState, CircuitState], None] | None = None,
    ):
        self.name = name
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._half_open_max_calls = half_open_max_calls
        self._clock = clock or _monotonic
        self._on_transition = on_transition

        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._half_open_calls_in_flight = 0
        self._half_open_successes = 0

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state

    def _maybe_transition_to_half_open(self) -> None:
        cooldown_elapsed = (
            self._state is CircuitState.OPEN
            and self._opened_at is not None
            and self._clock() - self._opened_at >= self._cooldown_seconds
        )
        if cooldown_elapsed:
            self._set_state(CircuitState.HALF_OPEN)
            self._half_open_calls_in_flight = 0
            self._half_open_successes = 0

    def _set_state(self, new_state: CircuitState) -> None:
        old_state = self._state
        if old_state == new_state:
            return
        self._state = new_state
        if self._on_transition:
            self._on_transition(self.name, old_state, new_state)

    def allow_request(self) -> bool:
        """Call before making the upstream request. Returns False if the
        circuit is open (or the half-open probe slots are full)."""
        with self._lock:
            self._maybe_transition_to_half_open()
            if self._state is CircuitState.CLOSED:
                return True
            if self._state is CircuitState.OPEN:
                return False
            # HALF_OPEN: allow only a bounded number of concurrent probes.
            if self._half_open_calls_in_flight < self._half_open_max_calls:
                self._half_open_calls_in_flight += 1
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            if self._state is CircuitState.HALF_OPEN:
                self._half_open_successes += 1
                self._half_open_calls_in_flight = max(0, self._half_open_calls_in_flight - 1)
                if self._half_open_successes >= self._half_open_max_calls:
                    self._set_state(CircuitState.CLOSED)
                    self._consecutive_failures = 0
            else:
                self._consecutive_failures = 0

    def record_failure(self) -> None:
        with self._lock:
            if self._state is CircuitState.HALF_OPEN:
                self._half_open_calls_in_flight = max(0, self._half_open_calls_in_flight - 1)
                self._trip_open()
                return

            self._consecutive_failures += 1
            if self._consecutive_failures >= self._failure_threshold:
                self._trip_open()

    def _trip_open(self) -> None:
        self._set_state(CircuitState.OPEN)
        self._opened_at = self._clock()
        self._consecutive_failures = 0
        self._half_open_calls_in_flight = 0
        self._half_open_successes = 0


def _monotonic() -> float:
    import time

    return time.monotonic()


class CircuitBreakerRegistry:
    """Holds one breaker per upstream route name, created lazily."""

    def __init__(self, on_transition: Callable[[str, CircuitState, CircuitState], None] | None = None):
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()
        self._on_transition = on_transition

    def get_or_create(
        self,
        name: str,
        failure_threshold: int,
        cooldown_seconds: float,
        half_open_max_calls: int,
    ) -> CircuitBreaker:
        with self._lock:
            breaker = self._breakers.get(name)
            if breaker is None:
                breaker = CircuitBreaker(
                    name=name,
                    failure_threshold=failure_threshold,
                    cooldown_seconds=cooldown_seconds,
                    half_open_max_calls=half_open_max_calls,
                    on_transition=self._on_transition,
                )
                self._breakers[name] = breaker
            return breaker

    def all(self) -> dict[str, CircuitBreaker]:
        with self._lock:
            return dict(self._breakers)
