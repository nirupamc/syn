"""M4 tests: AdmissionController unit tests (no HTTP, no backend)."""

from __future__ import annotations

import asyncio

import pytest

from app.core.admission import AdmissionController
from app.core.errors import QueueFullError, QueueTimeoutError


# ---- Basic status ----------------------------------------------------------


async def test_initial_status():
    ctrl = AdmissionController(max_active_requests=2, max_queue_size=4, queue_timeout_seconds=10.0)
    status = await ctrl.status()
    assert status.active == 0
    assert status.queued == 0
    assert status.max_active == 2
    assert status.max_queue == 4


# ---- Single acquire/release ------------------------------------------------


async def test_single_acquire_increments_active():
    ctrl = AdmissionController(max_active_requests=2, max_queue_size=4, queue_timeout_seconds=10.0)
    async with ctrl.acquire():
        status = await ctrl.status()
        assert status.active == 1
    status = await ctrl.status()
    assert status.active == 0


async def test_active_count_returns_to_zero():
    ctrl = AdmissionController(max_active_requests=2, max_queue_size=4, queue_timeout_seconds=10.0)
    async with ctrl.acquire():
        pass
    status = await ctrl.status()
    assert status.active == 0


# ---- Active limit enforcement ----------------------------------------------


async def test_active_limit_enforced():
    """With max_active=2, only 2 requests run simultaneously."""
    ctrl = AdmissionController(max_active_requests=2, max_queue_size=10, queue_timeout_seconds=10.0)
    entered = 0
    release = asyncio.Event()
    completed: list[int] = []

    async def worker(i: int):
        nonlocal entered
        async with ctrl.acquire():
            entered += 1
            try:
                await release.wait()
            finally:
                completed.append(i)

    tasks = [asyncio.create_task(worker(i)) for i in range(3)]
    await asyncio.sleep(0.1)  # let first two acquire
    assert entered == 2
    assert (await ctrl.status()).active == 2
    assert (await ctrl.status()).queued == 1

    release.set()
    await asyncio.gather(*tasks)
    assert (await ctrl.status()).active == 0
    assert (await ctrl.status()).queued == 0
    assert sorted(completed) == [0, 1, 2]


# ---- FIFO queue order ------------------------------------------------------


async def test_fifo_queue_order():
    """Queued requests are admitted in FIFO order."""
    ctrl = AdmissionController(max_active_requests=1, max_queue_size=10, queue_timeout_seconds=10.0)
    release = asyncio.Event()
    order: list[int] = []

    async def worker(i: int):
        async with ctrl.acquire():
            await release.wait()
            order.append(i)

    tasks = [asyncio.create_task(worker(i)) for i in range(4)]
    await asyncio.sleep(0.1)  # let one acquire, three queue
    release.set()
    await asyncio.gather(*tasks)
    # First one in, then 0, 1, 2, 3 in order
    assert order == [0, 1, 2, 3]


# ---- Queue full ------------------------------------------------------------


async def test_queue_full_rejects():
    """When queue is at capacity, new requests are rejected with QueueFullError."""
    ctrl = AdmissionController(max_active_requests=1, max_queue_size=1, queue_timeout_seconds=10.0)
    release = asyncio.Event()

    async def hold():
        async with ctrl.acquire():
            await release.wait()

    t1 = asyncio.create_task(hold())
    await asyncio.sleep(0.05)  # let t1 acquire

    # Start t2 (fills the queue)
    t2 = asyncio.create_task(hold())
    await asyncio.sleep(0.05)

    status = await ctrl.status()
    assert status.active == 1
    assert status.queued == 1

    # Now try to acquire a third — should fail immediately
    with pytest.raises(QueueFullError):
        async with ctrl.acquire():
            pass

    release.set()
    await asyncio.gather(t1, t2)


# ---- Queue timeout ---------------------------------------------------------


async def test_queue_timeout():
    """A queued request that waits too long is rejected with QueueTimeoutError."""
    ctrl = AdmissionController(
        max_active_requests=1, max_queue_size=2, queue_timeout_seconds=0.2
    )
    release = asyncio.Event()

    async def hold():
        async with ctrl.acquire():
            await release.wait()

    t1 = asyncio.create_task(hold())
    await asyncio.sleep(0.05)  # let t1 acquire

    # t2 tries to acquire and should time out
    with pytest.raises(QueueTimeoutError):
        async with ctrl.acquire():
            pass

    # Queue count should be back to 0
    status = await ctrl.status()
    assert status.queued == 0

    release.set()
    await t1


# ---- Slot release after backend failure -----------------------------------


async def test_slot_release_after_exception():
    """If the work inside `acquire()` raises, the slot is released."""
    ctrl = AdmissionController(max_active_requests=1, max_queue_size=4, queue_timeout_seconds=10.0)

    with pytest.raises(RuntimeError):
        async with ctrl.acquire():
            raise RuntimeError("backend failed")

    # Slot should be free
    async with ctrl.acquire():
        pass
    status = await ctrl.status()
    assert status.active == 0


async def test_slot_release_after_backend_error_then_next_runs():
    """After a backend failure, the next queued request can still run."""
    ctrl = AdmissionController(max_active_requests=1, max_queue_size=4, queue_timeout_seconds=10.0)
    ran = []

    async def fail():
        async with ctrl.acquire():
            ran.append("fail")
            raise ValueError("boom")

    async def succeed():
        async with ctrl.acquire():
            ran.append("ok")

    with pytest.raises(ValueError):
        await fail()
    await succeed()
    assert ran == ["fail", "ok"]


# ---- Concurrency race test -------------------------------------------------


async def test_concurrent_acquires_never_exceed_max():
    """Launch many concurrent acquires; observed max active never exceeds limit."""
    ctrl = AdmissionController(max_active_requests=3, max_queue_size=20, queue_timeout_seconds=10.0)
    peak = 0
    release = asyncio.Event()

    async def worker():
        nonlocal peak
        async with ctrl.acquire():
            current = (await ctrl.status()).active
            peak = max(peak, current)
            await release.wait()

    tasks = [asyncio.create_task(worker()) for _ in range(20)]
    await asyncio.sleep(0.2)  # let all acquire/queue
    release.set()
    await asyncio.gather(*tasks)
    assert peak <= 3
    assert peak >= 1  # at least one should have run


# ---- Slot release on cancellation ------------------------------------------


async def test_slot_release_on_cancellation():
    """If a task is cancelled while holding a slot, the slot is released."""
    ctrl = AdmissionController(max_active_requests=1, max_queue_size=2, queue_timeout_seconds=10.0)

    async def hold():
        async with ctrl.acquire():
            await asyncio.sleep(100)

    t = asyncio.create_task(hold())
    await asyncio.sleep(0.05)
    assert (await ctrl.status()).active == 1

    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass

    # Slot should be free
    status = await ctrl.status()
    assert status.active == 0


# ---- Multiple acquires from same task --------------------------------------


async def test_sequential_acquires_release_correctly():
    """Sequential acquire/release cycles return the slot each time."""
    ctrl = AdmissionController(max_active_requests=1, max_queue_size=0, queue_timeout_seconds=10.0)
    for _ in range(5):
        async with ctrl.acquire():
            pass
    status = await ctrl.status()
    assert status.active == 0
    assert status.queued == 0
