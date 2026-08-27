"""In-process request rate limiter (M6).

Implements a simple fixed-window-per-key request rate limiter with an
injectable clock. The limiter is process-local (M4 single-worker model).

For each (key, window_start) pair we track the count of admitted requests.
At admission time we look up the current window and reject if the count
already meets/exceeds the limit.

Limits of 0 (or falsy) mean unlimited and are never enforced.

This is deliberately simple:
* No sliding window
* No cross-process coordination
* No Redis
* No per-second resolution (per-minute is sufficient for M6)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class RateLimitResult:
    """Result of a rate-limit check."""

    allowed: bool
    remaining: int  # requests remaining in current window; -1 if unlimited
    retry_after_seconds: int  # seconds until window resets; 0 if allowed


class RateLimiter:
    """Process-local per-key request rate limiter (fixed window per minute).

    The clock is injectable for deterministic testing. The default clock
    is ``time.monotonic``, which is appropriate for measuring elapsed
    intervals (not wall-clock time).
    """

    def __init__(
        self,
        window_seconds: int = 60,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._window_seconds = window_seconds
        self._clock = clock or time.monotonic
        # Maps key -> (window_start, count)
        self._windows: dict[str, tuple[float, int]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _window_key(key: str, now: float, window_seconds: int) -> tuple[float, int]:
        """Compute the window start for a key at a given time."""
        return (now - (now % window_seconds), 0)

    async def check(self, key: str, limit: int) -> RateLimitResult:
        """Check whether a request for ``key`` is allowed under ``limit``.

        ``limit <= 0`` means unlimited and always returns allowed.
        """
        if limit <= 0:
            return RateLimitResult(allowed=True, remaining=-1, retry_after_seconds=0)

        async with self._lock:
            now = self._clock()
            window_start = now - (now % self._window_seconds)
            current = self._windows.get(key)
            if current is None or current[0] != window_start:
                # New window
                self._windows[key] = (window_start, 1)
                return RateLimitResult(
                    allowed=True,
                    remaining=limit - 1,
                    retry_after_seconds=0,
                )
            else:
                count = current[1]
                if count >= limit:
                    # Over limit; compute retry-after
                    retry_after = int(window_start + self._window_seconds - now)
                    if retry_after < 1:
                        retry_after = 1
                    return RateLimitResult(
                        allowed=False,
                        remaining=0,
                        retry_after_seconds=retry_after,
                    )
                self._windows[key] = (window_start, count + 1)
                return RateLimitResult(
                    allowed=True,
                    remaining=limit - count - 1,
                    retry_after_seconds=0,
                )

    async def reset(self) -> None:
        """Clear all tracked windows. Useful for tests."""
        async with self._lock:
            self._windows.clear()

    async def current_count(self, key: str) -> int:
        """Return the current window count for a key (for testing/inspection)."""
        async with self._lock:
            now = self._clock()
            window_start = now - (now % self._window_seconds)
            current = self._windows.get(key)
            if current is None or current[0] != window_start:
                return 0
            return current[1]
