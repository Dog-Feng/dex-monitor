from __future__ import annotations

import time


class RateLimiter:
    def __init__(self, min_interval_seconds: float = 0.1):
        self.min_interval = min_interval_seconds
        self._last_call = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.monotonic()
