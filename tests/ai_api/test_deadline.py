"""The deadline of a digest request: the time left, and the timeouts of a request shortened to
it."""

import httpx2

from k_b.ai_api.deadline import Deadline
from k_b.core.config import MIN_SECONDS_FOR_LLM_REQUEST


class Clock:
    """A clock set by the test: never the time of the machine."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_a_deadline_counts_down_from_its_start():
    clock = Clock()
    deadline = Deadline.after(200, clock)

    clock.now += 150

    assert deadline.remaining_seconds() == 50
    assert deadline.is_passed() is False
    clock.now += 50
    assert deadline.is_passed() is True


def test_a_request_to_the_model_needs_the_minimum_left_wait_included():
    clock = Clock()
    deadline = Deadline.after(MIN_SECONDS_FOR_LLM_REQUEST + 5, clock)

    assert deadline.allows_llm_request() is True
    assert deadline.allows_llm_request(wait_seconds=5) is True
    assert deadline.allows_llm_request(wait_seconds=6) is False
    clock.now += 6
    assert deadline.allows_llm_request() is False


def test_the_timeouts_are_shortened_to_the_time_left_never_lengthened():
    clock = Clock()
    deadline = Deadline.after(30, clock)
    timeout = httpx2.Timeout(connect=5, read=90, write=10, pool=None)

    capped = deadline.cap(timeout)

    assert (capped.connect, capped.read, capped.write, capped.pool) == (5, 30, 10, 30)
    clock.now += 40
    passed = deadline.cap(timeout)
    assert (passed.connect, passed.read, passed.write, passed.pool) == (0, 0, 0, 0)
