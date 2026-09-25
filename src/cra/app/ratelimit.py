"""Fixed-window counters, in memory.

Enough to keep the cluster's model budget away from a crawler, and deliberately
process-local: the limits are cost control, not a security boundary, and a
restart resetting them is acceptable. A shared backend can replace this class
without the callers noticing.
"""

import time
from dataclasses import dataclass

DAY_S = 24 * 60 * 60


@dataclass(frozen=True)
class Allowance:
    allowed: bool
    remaining: int
    reset_in_s: int

    @property
    def retry_after(self) -> int:
        return max(1, self.reset_in_s)


class RateLimiter:
    def __init__(self, clock=time.monotonic) -> None:
        # (key, window length, window number): windows of different lengths
        # are numbered on different scales and must never be compared
        self._counts: dict[tuple[str, int, int], int] = {}
        self._clock = clock

    def _window(self, window_s: int) -> int:
        return int(self._clock() // window_s)

    def check(self, key: str, limit: int, window_s: int = DAY_S) -> Allowance:
        """Count one request against ``key``; a limit of 0 means no limit."""
        if limit <= 0:
            return Allowance(True, -1, 0)
        window = self._window(window_s)
        self._forget_older_windows(window_s, window)
        used = self._counts.get((key, window_s, window), 0)
        if used >= limit:
            elapsed = self._clock() % window_s
            return Allowance(False, 0, int(window_s - elapsed))
        self._counts[(key, window_s, window)] = used + 1
        return Allowance(
            True, limit - used - 1, int(window_s - (self._clock() % window_s))
        )

    def peek(self, key: str, limit: int, window_s: int = DAY_S) -> Allowance:
        """Whether ``key`` is within ``limit``, without counting a request:
        for limits that count only failures, which a caller records with
        ``check`` once it knows."""
        if limit <= 0:
            return Allowance(True, -1, 0)
        used = self._counts.get((key, window_s, self._window(window_s)), 0)
        reset_in = int(window_s - (self._clock() % window_s))
        return Allowance(used < limit, max(0, limit - used), reset_in)

    def _forget_older_windows(self, window_s: int, current: int) -> None:
        stale = [k for k in self._counts if k[1] == window_s and k[2] < current]
        for key in stale:
            del self._counts[key]
