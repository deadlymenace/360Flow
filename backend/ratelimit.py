"""In-memory request limits keyed by a verified device or a client IP."""

from __future__ import annotations

import time


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}
        self._max_window = 60.0
        self._last_prune = 0.0

    def reset(self) -> None:
        self._hits.clear()
        self._last_prune = 0.0

    def allow(self, key: str, limit: int, window_seconds: float, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        self._max_window = max(self._max_window, window_seconds)
        self._prune(current)
        recent = [stamp for stamp in self._hits.get(key, []) if current - stamp < window_seconds]
        if len(recent) >= limit:
            if recent:
                self._hits[key] = recent
            else:
                self._hits.pop(key, None)
            return False
        recent.append(current)
        self._hits[key] = recent
        return True

    def _prune(self, now: float) -> None:
        """Drop keys whose newest hit is outside the longest window we have seen."""
        if now - self._last_prune < 30 and self._last_prune:
            return
        self._last_prune = now
        expired = [
            key
            for key, stamps in self._hits.items()
            if not stamps or now - stamps[-1] >= self._max_window
        ]
        for key in expired:
            del self._hits[key]


limiter = RateLimiter()
