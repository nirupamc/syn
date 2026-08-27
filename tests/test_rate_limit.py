"""M6 tests: rate limiter unit tests."""

from __future__ import annotations

import pytest

from app.core.rate_limit import RateLimiter, RateLimitResult


async def test_unlimited_when_limit_zero():
    limiter = RateLimiter(window_seconds=60, clock=lambda: 0.0)
    for _ in range(100):
        result = await limiter.check("key1", limit=0)
        assert result.allowed is True
        assert result.remaining == -1


async def test_first_request_allowed():
    limiter = RateLimiter(window_seconds=60, clock=lambda: 0.0)
    result = await limiter.check("key1", limit=2)
    assert result.allowed is True
    assert result.remaining == 1


async def test_second_request_allowed():
    limiter = RateLimiter(window_seconds=60, clock=lambda: 0.0)
    await limiter.check("key1", limit=2)
    result = await limiter.check("key1", limit=2)
    assert result.allowed is True
    assert result.remaining == 0


async def test_third_request_rejected():
    limiter = RateLimiter(window_seconds=60, clock=lambda: 0.0)
    await limiter.check("key1", limit=2)
    await limiter.check("key1", limit=2)
    result = await limiter.check("key1", limit=2)
    assert result.allowed is False
    assert result.remaining == 0
    assert result.retry_after_seconds > 0


async def test_window_reset():
    clock_value = [0.0]

    def clock():
        return clock_value[0]

    limiter = RateLimiter(window_seconds=60, clock=clock)
    await limiter.check("key1", limit=1)
    result = await limiter.check("key1", limit=1)
    assert result.allowed is False

    # Advance past the window
    clock_value[0] = 61.0
    result = await limiter.check("key1", limit=1)
    assert result.allowed is True


async def test_keys_independent():
    limiter = RateLimiter(window_seconds=60, clock=lambda: 0.0)
    await limiter.check("key1", limit=1)
    # key1 is at limit, but key2 is fresh
    result = await limiter.check("key2", limit=1)
    assert result.allowed is True


async def test_retry_after_accurate():
    clock_value = [0.0]

    def clock():
        return clock_value[0]

    limiter = RateLimiter(window_seconds=60, clock=clock)
    await limiter.check("key1", limit=1)
    # At t=30, we should have ~30s remaining
    clock_value[0] = 30.0
    result = await limiter.check("key1", limit=1)
    assert result.allowed is False
    assert result.retry_after_seconds == 30


async def test_reset_clears_all_windows():
    limiter = RateLimiter(window_seconds=60, clock=lambda: 0.0)
    await limiter.check("key1", limit=1)
    await limiter.check("key2", limit=1)
    await limiter.reset()
    assert await limiter.current_count("key1") == 0
    assert await limiter.current_count("key2") == 0


async def test_concurrent_access_safe():
    import asyncio

    limiter = RateLimiter(window_seconds=60, clock=lambda: 0.0)

    async def hit():
        return await limiter.check("key1", limit=5)

    # 20 concurrent requests against limit=5
    results = await asyncio.gather(*[hit() for _ in range(20)])
    allowed = sum(1 for r in results if r.allowed)
    rejected = sum(1 for r in results if not r.allowed)
    assert allowed == 5
    assert rejected == 15
