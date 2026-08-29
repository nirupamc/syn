"""Backend registry (M9).

Holds the configured backend *instances* keyed by their configured backend id
(not by BackendType). Provides O(1) lookup, listing, and a small health cache
with a TTL so callers (admin status, optional fail-fast) can read the last
known health without re-probing on every request.

Routing never trusts the cache for correctness: an unavailable backend is
detected authoritatively when the request is actually dispatched (the backend
raises ``BackendUnavailableError``). The cache is an observability/status aid.
"""

from __future__ import annotations

import time
from typing import Optional

from app.backends.base import BackendHealthResult, InferenceBackend
from app.core.errors import BackendNotConfiguredError


class BackendRegistry:
    """Registry of configured backend instances by backend id."""

    def __init__(self, *, health_ttl_seconds: float = 5.0) -> None:
        self._backends: dict[str, InferenceBackend] = {}
        self._health_cache: dict[str, BackendHealthResult] = {}
        self._health_at: dict[str, float] = {}
        self._health_ttl = health_ttl_seconds

    def register(self, backend_id: str, backend: InferenceBackend) -> None:
        """Register a backend instance under ``backend_id``."""
        self._backends[backend_id] = backend

    def has(self, backend_id: str) -> bool:
        return backend_id in self._backends

    def ids(self) -> list[str]:
        return list(self._backends)

    def get(self, backend_id: str) -> InferenceBackend:
        """Return the backend instance, or raise ``backend_not_configured``."""
        backend = self._backends.get(backend_id)
        if backend is None:
            raise BackendNotConfiguredError(
                f"backend '{backend_id}' is not configured",
                code="backend_not_configured",
            )
        return backend

    def types(self) -> dict[str, str]:
        """Map backend id -> backend type name (for status reporting)."""
        return {bid: b.name for bid, b in self._backends.items()}

    async def health(
        self, backend_id: str, *, force: bool = False
    ) -> BackendHealthResult:
        """Return cached (or freshly probed) health for a backend.

        Raises ``backend_not_configured`` if the id is unknown. Probes live
        when ``force`` is set or the cache is stale/missing.
        """
        backend = self.get(backend_id)
        now = time.monotonic()
        cached = self._health_cache.get(backend_id)
        cached_at = self._health_at.get(backend_id, 0.0)
        if not force and cached is not None and (now - cached_at) <= self._health_ttl:
            return cached
        try:
            result = await backend.health()
        except Exception:  # noqa: BLE001 - health must never crash the caller
            from app.backends.base import BackendHealthState

            result = BackendHealthResult(
                state=BackendHealthState.UNREACHABLE,
                reachable=False,
                reason="health probe raised",
            )
        self._health_cache[backend_id] = result
        self._health_at[backend_id] = now
        return result

    async def health_map(self, *, force: bool = False) -> dict[str, BackendHealthResult]:
        """Return live (or cached) health for all backends, keyed by id."""
        out: dict[str, BackendHealthResult] = {}
        for bid in self._backends:
            out[bid] = await self.health(bid, force=force)
        return out
