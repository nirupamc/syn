"""M9 tests for backend registry: register, get, health, health_map."""

from __future__ import annotations

import asyncio

import pytest

from app.backends.base import BackendHealthResult, BackendHealthState, InferenceBackend
from app.core.errors import BackendNotConfiguredError
from app.routing.backend_registry import BackendRegistry


class StubBackend:
    """Minimal InferenceBackend stub for testing the registry."""

    name = "stub"

    def __init__(self, *, reachable: bool = True):
        self._reachable = reachable

    async def health(self) -> BackendHealthResult:
        if self._reachable:
            return BackendHealthResult(
                state=BackendHealthState.REACHABLE,
                reachable=True,
                reason="ok",
                model="stub-model",
                server_version="1.0",
            )
        return BackendHealthResult(
            state=BackendHealthState.UNREACHABLE,
            reachable=False,
            reason="down",
        )

    async def models(self):
        return []

    async def open(self):
        pass

    async def close(self):
        pass

    def capabilities(self):
        return []

    async def chat_completion(self, req):
        pass

    async def stream_chat_completion(self, req):
        yield {}


class TestBackendRegistryBasic:
    def test_register_and_get(self):
        reg = BackendRegistry()
        backend = StubBackend()
        reg.register("gpu-1", backend)
        assert reg.get("gpu-1") is backend

    def test_get_unknown_raises(self):
        reg = BackendRegistry()
        with pytest.raises(BackendNotConfiguredError, match="not configured"):
            reg.get("nonexistent")

    def test_has(self):
        reg = BackendRegistry()
        reg.register("a", StubBackend())
        assert reg.has("a") is True
        assert reg.has("b") is False

    def test_ids(self):
        reg = BackendRegistry()
        reg.register("a", StubBackend())
        reg.register("b", StubBackend())
        assert set(reg.ids()) == {"a", "b"}

    def test_types(self):
        reg = BackendRegistry()
        reg.register("a", StubBackend())
        types = reg.types()
        assert types["a"] == "stub"


@pytest.mark.asyncio
class TestBackendRegistryHealth:
    async def test_health_probe_reachable(self):
        reg = BackendRegistry()
        reg.register("a", StubBackend(reachable=True))
        result = await reg.health("a", force=True)
        assert result.reachable is True
        assert result.state == BackendHealthState.REACHABLE

    async def test_health_probe_unreachable(self):
        reg = BackendRegistry()
        reg.register("a", StubBackend(reachable=False))
        result = await reg.health("a", force=True)
        assert result.reachable is False

    async def test_health_caching(self):
        reg = BackendRegistry(health_ttl_seconds=10.0)
        reg.register("a", StubBackend(reachable=True))
        # First call: probes live
        r1 = await reg.health("a", force=True)
        assert r1.reachable is True
        # Second call without force: should return cached
        r2 = await reg.health("a", force=False)
        assert r2.reachable is True

    async def test_health_force_refresh(self):
        reg = BackendRegistry(health_ttl_seconds=999.0)
        reg.register("a", StubBackend(reachable=True))
        await reg.health("a", force=True)
        # Force refresh should re-probe (still reachable)
        r = await reg.health("a", force=True)
        assert r.reachable is True

    async def test_health_unknown_backend_raises(self):
        reg = BackendRegistry()
        with pytest.raises(BackendNotConfiguredError):
            await reg.health("nonexistent")

    async def test_health_exception_maps_to_unreachable(self):
        class BrokenBackend:
            name = "broken"

            async def health(self):
                raise RuntimeError("probe exploded")

        reg = BackendRegistry()
        reg.register("b", BrokenBackend())
        result = await reg.health("b", force=True)
        assert result.reachable is False
        assert result.state == BackendHealthState.UNREACHABLE

    async def test_health_map(self):
        reg = BackendRegistry()
        reg.register("a", StubBackend(reachable=True))
        reg.register("b", StubBackend(reachable=False))
        hmap = await reg.health_map()
        assert hmap["a"].reachable is True
        assert hmap["b"].reachable is False
        assert len(hmap) == 2
