"""Circuit breaker tests use a fake, manually-advanced clock instead of
real sleeps so the cooldown/half-open behavior is deterministic and fast.
"""

from __future__ import annotations

from gatekeeper.proxy.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry, CircuitState


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_breaker(**overrides) -> tuple[CircuitBreaker, FakeClock]:
    clock = FakeClock()
    defaults = {
        "name": "test-route",
        "failure_threshold": 3,
        "cooldown_seconds": 10,
        "half_open_max_calls": 2,
        "clock": clock,
    }
    defaults.update(overrides)
    return CircuitBreaker(**defaults), clock


def test_starts_closed_and_allows_requests():
    breaker, _ = make_breaker()
    assert breaker.state == CircuitState.CLOSED
    assert breaker.allow_request() is True


def test_trips_open_after_failure_threshold_consecutive_failures():
    breaker, _ = make_breaker(failure_threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.CLOSED
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN


def test_success_resets_consecutive_failure_count():
    breaker, _ = make_breaker(failure_threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    # Only 2 consecutive failures since the reset -- still closed.
    assert breaker.state == CircuitState.CLOSED


def test_open_circuit_rejects_requests_without_a_probe():
    breaker, clock = make_breaker(failure_threshold=1, cooldown_seconds=10)
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    assert breaker.allow_request() is False


def test_transitions_to_half_open_after_cooldown():
    breaker, clock = make_breaker(failure_threshold=1, cooldown_seconds=10)
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN

    clock.advance(9.99)
    assert breaker.state == CircuitState.OPEN

    clock.advance(0.02)
    assert breaker.state == CircuitState.HALF_OPEN


def test_half_open_allows_only_bounded_number_of_probes():
    breaker, clock = make_breaker(failure_threshold=1, cooldown_seconds=10, half_open_max_calls=2)
    breaker.record_failure()
    clock.advance(10)
    assert breaker.state == CircuitState.HALF_OPEN

    assert breaker.allow_request() is True
    assert breaker.allow_request() is True
    assert breaker.allow_request() is False  # 3rd probe exceeds the cap


def test_half_open_failure_reopens_immediately():
    breaker, clock = make_breaker(failure_threshold=1, cooldown_seconds=10, half_open_max_calls=2)
    breaker.record_failure()
    clock.advance(10)
    assert breaker.state == CircuitState.HALF_OPEN

    assert breaker.allow_request() is True
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN


def test_half_open_closes_after_enough_successful_probes():
    breaker, clock = make_breaker(failure_threshold=1, cooldown_seconds=10, half_open_max_calls=2)
    breaker.record_failure()
    clock.advance(10)
    assert breaker.state == CircuitState.HALF_OPEN

    assert breaker.allow_request() is True
    breaker.record_success()
    assert breaker.state == CircuitState.HALF_OPEN  # need both probes to succeed

    assert breaker.allow_request() is True
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED


def test_on_transition_callback_is_invoked():
    transitions = []
    clock = FakeClock()
    breaker = CircuitBreaker(
        name="r",
        failure_threshold=1,
        cooldown_seconds=5,
        half_open_max_calls=1,
        clock=clock,
        on_transition=lambda name, old, new: transitions.append((name, old, new)),
    )
    breaker.record_failure()
    assert transitions == [("r", CircuitState.CLOSED, CircuitState.OPEN)]


def test_registry_reuses_breaker_for_same_route_name():
    registry = CircuitBreakerRegistry()
    b1 = registry.get_or_create("route-a", 5, 10, 2)
    b2 = registry.get_or_create("route-a", 5, 10, 2)
    assert b1 is b2


def test_registry_gives_separate_breakers_per_route():
    registry = CircuitBreakerRegistry()
    b1 = registry.get_or_create("route-a", 5, 10, 2)
    b2 = registry.get_or_create("route-b", 5, 10, 2)
    assert b1 is not b2
    b1.record_failure()
    assert b2.state == CircuitState.CLOSED
