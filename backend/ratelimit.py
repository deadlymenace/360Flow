"""In-memory request limits keyed by device token or client IP."""

from __future__ import annotations

import time
from collections import defaultdict


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)

    def allow(self, key: str, limit: int, window_seconds: float) -> bool:
        now = time.monotonic()
        recent = [stamp for stamp in self._hits[key] if now - stamp < window_seconds]
        if len(recent) >= limit:
            self._hits[key] = recent
            return False
        recent.append(now)
        self._hits[key] = recent
        return True


limiter = RateLimiter()
