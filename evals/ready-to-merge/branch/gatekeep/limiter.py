"""Fixed-window request limiting, as specified in docs/spec/limiter.md."""

import math
import time
from collections.abc import Callable


class Limiter:
    def __init__(
        self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._counts: dict[str, tuple[float, int]] = {}

    def allow(self, key: str) -> bool:
        now = self._clock()
        current_start = math.floor(now / self._window) * self._window
        start, count = self._counts.get(key, (current_start, 0))
        if now > start + self._window:
            start, count = current_start, 0
        if count >= self._limit:
            self._counts[key] = (start, count)
            return False
        self._counts[key] = (start, count + 1)
        return True
