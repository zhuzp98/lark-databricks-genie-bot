"""Simple process-local rate limiter for Genie POST (~5 QPM Free Edition)."""

from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, min_interval_sec: float = 13.0):
        self.min_interval_sec = min_interval_sec
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self.min_interval_sec - (now - self._last)
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()
