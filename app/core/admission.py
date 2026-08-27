"""Admission controller (M4).

Single-process, in-memory admission control for chat-completion requests.

This is an **admission controller**, not an inference scheduler. Syn decides
whether a request is allowed to reach the backend now, whether it waits in
a bounded queue, or whether it is rejected. llama.cpp owns actual inference
scheduling and continuous batching internally.

Properties:
* At most ``max_active_requests`` requests may execute concurrently.
* At most ``max_queue_size`` requests may wait in the FIFO queue.
* A queued request that waits longer than ``queue_timeout_seconds`` is
  rejected with ``QueueTimeoutError``.
* A request that finds the queue full is rejected immediately with
  ``QueueFullError``.
* Slots are released on success, backend error, or any unexpected exception
  (``async with`` / try/finally semantics).
* State is in-memory only. On restart, active/queued requests are lost.
* This is process-local. Running multiple Uvicorn workers would create
  independent admission controllers and violate the global concurrency
  guarantee. Syn must run with ONE worker.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Optional

from app.core.errors import QueueFullError, QueueTimeoutError
from app.logging import get_logger

logger = get_logger("syn.admission")


@dataclass
class AdmissionStatus:
    """Snapshot of admission controller state."""

    active: int
    max_active: int
    queued: int
    max_queue: int
    queue_timeout_seconds: float


@dataclass
class _QueueEntry:
    """Internal record for a queued request waiting for a slot."""

    event: asyncio.Event
    granted: bool = False
    enqueued_at: float = 0.0


class AdmissionController:
    """Single-process admission controller for inference requests.

    Uses an ``asyncio.Semaphore`` to limit concurrent execution and an
    explicit bounded FIFO queue for waiting requests. We deliberately do
    NOT rely on the semaphore's internal waiting queue because
    ``asyncio.Semaphore`` does not expose its waiters and does not support
    a bounded capacity or per-waiter timeouts. We maintain our own queue
    and a single ``asyncio.Event`` per waiter.
    """

    def __init__(
        self,
        max_active_requests: int,
        max_queue_size: int,
        queue_timeout_seconds: float,
    ) -> None:
        self._max_active = max_active_requests
        self._max_queue = max_queue_size
        self._queue_timeout = queue_timeout_seconds
        self._sem = asyncio.Semaphore(max_active_requests)
        self._queue: list[_QueueEntry] = []
        self._active = 0  # running count (mirrors semaphore state for visibility)
        self._lock = asyncio.Lock()  # guards _queue and _active

    @property
    def max_active(self) -> int:
        return self._max_active

    @property
    def max_queue(self) -> int:
        return self._max_queue

    @property
    def queue_timeout_seconds(self) -> float:
        return self._queue_timeout

    async def status(self) -> AdmissionStatus:
        async with self._lock:
            return AdmissionStatus(
                active=self._active,
                max_active=self._max_active,
                queued=len(self._queue),
                max_queue=self._max_queue,
                queue_timeout_seconds=self._queue_timeout,
            )

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        """Acquire an execution slot, waiting in the bounded queue if needed.

        Raises:
            QueueFullError: if the queue is at capacity.
            QueueTimeoutError: if the request waits longer than
                ``queue_timeout_seconds``.
        """
        # Fast path: try to acquire without waiting.
        if self._sem.locked() or self._queue:
            # Either someone is running and the queue may not be empty,
            # or there are already waiters. Take the queue path.
            entry: Optional[_QueueEntry] = None
            async with self._lock:
                # Re-check: a slot may have freed up between the check
                # above and the lock acquisition.
                if (
                    not self._sem.locked()
                    and not self._queue
                ):
                    # Take the fast path: acquire the semaphore now.
                    pass
                else:
                    if len(self._queue) >= self._max_queue:
                        raise QueueFullError(
                            "admission queue is full",
                            code="queue_full",
                            http_status=429,
                        )
                    entry = _QueueEntry(
                        event=asyncio.Event(),
                        enqueued_at=time.monotonic(),
                    )
                    self._queue.append(entry)

            if entry is not None:
                # Wait for our turn or for timeout.
                try:
                    await asyncio.wait_for(
                        entry.event.wait(), timeout=self._queue_timeout
                    )
                except asyncio.TimeoutError:
                    # Remove ourselves from the queue if still present.
                    await self._remove_queued_entry(entry)
                    raise QueueTimeoutError(
                        f"request waited longer than {self._queue_timeout}s in queue",
                        code="queue_timeout",
                        http_status=503,
                    ) from None
                if not entry.granted:
                    # Should not happen, but guard anyway.
                    raise QueueTimeoutError(
                        "queue entry was cancelled without grant",
                        code="queue_timeout",
                        http_status=503,
                    )

            # Acquire the semaphore (slot should now be available).
            await self._sem.acquire()
            async with self._lock:
                self._active += 1
        else:
            # No one is running and no one is queued: take the slot directly.
            await self._sem.acquire()
            async with self._lock:
                self._active += 1

        try:
            yield
        finally:
            # Release the slot and grant it to the next FIFO waiter.
            await self._release()

    async def _remove_queued_entry(self, entry: _QueueEntry) -> None:
        async with self._lock:
            try:
                self._queue.remove(entry)
            except ValueError:
                pass

    async def _release(self) -> None:
        """Release the active slot and grant it to the next FIFO waiter."""
        async with self._lock:
            self._active -= 1
            assert self._active >= 0, "active count went negative"
            # Grant the slot to the next FIFO waiter if any.
            if self._queue:
                next_entry = self._queue.pop(0)
                next_entry.granted = True
                next_entry.event.set()
        # Release the semaphore (outside the lock to avoid lock contention).
        self._sem.release()
