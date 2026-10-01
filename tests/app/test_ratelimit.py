import pytest

from cra.app.ratelimit import RateLimiter


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


def test_requests_are_counted_until_the_limit(clock):
    limiter = RateLimiter(clock)
    allowances = [limiter.check("a", limit=3, window_s=100) for _ in range(4)]
    assert [a.allowed for a in allowances] == [True, True, True, False]
    assert [a.remaining for a in allowances] == [2, 1, 0, 0]


def test_keys_are_counted_separately(clock):
    limiter = RateLimiter(clock)
    limiter.check("a", limit=1, window_s=100)
    assert limiter.check("b", limit=1, window_s=100).allowed is True
    assert limiter.check("a", limit=1, window_s=100).allowed is False


def test_the_window_resets(clock):
    limiter = RateLimiter(clock)
    assert limiter.check("a", limit=1, window_s=100).allowed is True
    refused = limiter.check("a", limit=1, window_s=100)
    assert refused.allowed is False
    assert refused.retry_after == 100
    clock.now = 100
    assert limiter.check("a", limit=1, window_s=100).allowed is True


def test_a_limit_of_zero_means_no_limit(clock):
    limiter = RateLimiter(clock)
    assert all(limiter.check("a", limit=0).allowed for _ in range(50))


def test_old_windows_are_forgotten(clock):
    limiter = RateLimiter(clock)
    for window in range(5):
        clock.now = window * 100
        limiter.check(f"key{window}", limit=2, window_s=100)
    assert len(limiter._counts) == 1


def test_windows_of_different_lengths_do_not_forget_each_other(clock):
    """A per-minute counter ticking over must leave the per-day counters alone:
    the MCP limiter and the chat limiter share one instance."""
    limiter = RateLimiter(clock)
    assert limiter.check("chat:u", limit=1, window_s=86_400).allowed is True
    clock.now = 61
    limiter.check("mcp:x", limit=10, window_s=60)
    assert limiter.check("chat:u", limit=1, window_s=86_400).allowed is False
