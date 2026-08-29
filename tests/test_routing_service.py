"""M9 tests for routing service: passthrough mode, configured mode, route resolution."""

from __future__ import annotations

import pytest

from app.backends.base import BackendHealthResult, BackendHealthState, InferenceBackend
from app.core.errors import (
    BackendNotConfiguredError,
    ModelDisabledError,
    ModelForbiddenError,
    ModelNotFoundError,
)
from app.core.principal import AuthenticatedPrincipal
from app.routing.backend_registry import BackendRegistry
from app.routing.model_registry import ModelEntry, ModelRegistry
from app.routing.router import RouteDecision, RoutingService


# ---- Stubs ------------------------------------------------------------------


class StubBackend:
    name = "stub"

    def __init__(self, models_list=None):
        self._models = models_list or []

    async def models(self):
        return self._models

    def capabilities(self):
        return []

    async def health(self):
        return BackendHealthResult(
            state=BackendHealthState.READY, reachable=True, reason="ok"
        )

    async def open(self):
        pass

    async def close(self):
        pass

    async def chat_completion(self, req):
        pass

    async def stream_chat_completion(self, req):
        yield {}


class StubModel:
    def __init__(self, id: str):
        self.id = id
        self.object = "model"
        self.owned_by = "stub"
        self.created = 0


class StubPrincipal:
    def __init__(self, allowed_models=None):
        self._allowed = allowed_models  # None = unrestricted

    def can_use_model(self, model_id: str) -> bool:
        if self._allowed is None:
            return True
        return model_id in self._allowed


# ---- Passthrough mode tests -------------------------------------------------


class TestRoutingPassthrough:
    def _make_service(self, backend_models=None):
        backend = StubBackend(models_list=[StubModel(m) for m in (backend_models or [])])
        return RoutingService(
            passthrough=True,
            get_default_backend=lambda: backend,
        ), backend

    def test_configured_false(self):
        svc, _ = self._make_service()
        assert svc.configured is False

    def test_backend_registry_none(self):
        svc, _ = self._make_service()
        assert svc.backend_registry is None

    @pytest.mark.asyncio
    async def test_list_models_filters_by_access(self):
        svc, _ = self._make_service(backend_models=["m1", "m2", "m3"])
        principal = StubPrincipal(allowed_models=["m1", "m3"])
        models = await svc.list_models(principal)
        ids = [m.id for m in models]
        assert ids == ["m1", "m3"]

    @pytest.mark.asyncio
    async def test_list_models_unrestricted(self):
        svc, _ = self._make_service(backend_models=["m1", "m2"])
        principal = StubPrincipal()
        models = await svc.list_models(principal)
        assert len(models) == 2

    @pytest.mark.asyncio
    async def test_route_canonical_match(self):
        svc, backend = self._make_service(backend_models=["model-a"])
        principal = StubPrincipal()
        decision = await svc.route("model-a", principal)
        assert decision.requested_model == "model-a"
        assert decision.canonical_model == "model-a"
        assert decision.backend_model == "model-a"
        assert decision.backend_id == "default"
        assert decision.reason == "passthrough"
        assert decision.backend is backend

    @pytest.mark.asyncio
    async def test_route_unknown_model_raises(self):
        svc, _ = self._make_service(backend_models=["model-a"])
        principal = StubPrincipal()
        with pytest.raises(ModelNotFoundError, match="not found"):
            await svc.route("nonexistent", principal)

    @pytest.mark.asyncio
    async def test_route_forbidden_model_raises(self):
        svc, _ = self._make_service(backend_models=["model-a"])
        principal = StubPrincipal(allowed_models=[])  # empty = nothing allowed
        with pytest.raises(ModelForbiddenError):
            await svc.route("model-a", principal)

    def test_require_default_backend_none_raises(self):
        svc = RoutingService(passthrough=True, get_default_backend=None)
        with pytest.raises(BackendNotConfiguredError):
            svc._require_default_backend()


# ---- Configured mode tests --------------------------------------------------


class TestRoutingConfigured:
    def _make_service(self, models, backend_models_map=None):
        backend = StubBackend()
        backend_registry = BackendRegistry()
        backend_registry.register("gpu-1", backend)
        model_registry = ModelRegistry(models)
        return RoutingService(
            model_registry=model_registry,
            backend_registry=backend_registry,
        ), backend, backend_registry

    def test_configured_true(self):
        entry = ModelEntry(id="m", backend_id="gpu-1", backend_model="m")
        svc, _, _ = self._make_service([entry])
        assert svc.configured is True

    def test_backend_registry_exposed(self):
        entry = ModelEntry(id="m", backend_id="gpu-1", backend_model="m")
        svc, _, br = self._make_service([entry])
        assert svc.backend_registry is br

    @pytest.mark.asyncio
    async def test_route_canonical(self):
        entry = ModelEntry(id="m", backend_id="gpu-1", backend_model="m.gguf")
        svc, backend, _ = self._make_service([entry])
        principal = StubPrincipal()
        decision = await svc.route("m", principal)
        assert decision.canonical_model == "m"
        assert decision.backend_model == "m.gguf"
        assert decision.backend_id == "gpu-1"
        assert decision.reason == "canonical_match"
        assert decision.backend is backend

    @pytest.mark.asyncio
    async def test_route_alias(self):
        entry = ModelEntry(id="m", backend_id="gpu-1", backend_model="m.gguf", aliases=("alias-m",))
        svc, backend, _ = self._make_service([entry])
        principal = StubPrincipal()
        decision = await svc.route("alias-m", principal)
        assert decision.requested_model == "alias-m"
        assert decision.canonical_model == "m"
        assert decision.reason == "alias_match"

    @pytest.mark.asyncio
    async def test_route_disabled_model_raises(self):
        entry = ModelEntry(id="m", backend_id="gpu-1", backend_model="m", enabled=False)
        svc, _, _ = self._make_service([entry])
        principal = StubPrincipal()
        with pytest.raises(ModelDisabledError):
            await svc.route("m", principal)

    @pytest.mark.asyncio
    async def test_route_forbidden_raises(self):
        entry = ModelEntry(id="m", backend_id="gpu-1", backend_model="m")
        svc, _, _ = self._make_service([entry])
        principal = StubPrincipal(allowed_models=[])
        with pytest.raises(ModelForbiddenError):
            await svc.route("m", principal)

    @pytest.mark.asyncio
    async def test_route_unknown_model_raises(self):
        entry = ModelEntry(id="m", backend_id="gpu-1", backend_model="m")
        svc, _, _ = self._make_service([entry])
        principal = StubPrincipal()
        with pytest.raises(ModelNotFoundError):
            await svc.route("nonexistent", principal)

    @pytest.mark.asyncio
    async def test_list_models_configured(self):
        entries = [
            ModelEntry(id="m1", backend_id="gpu-1", backend_model="m1"),
            ModelEntry(id="m2", backend_id="gpu-1", backend_model="m2", enabled=False),
            ModelEntry(id="m3", backend_id="gpu-1", backend_model="m3"),
        ]
        svc, _, _ = self._make_service(entries)
        principal = StubPrincipal()
        models = await svc.list_models(principal)
        # Only enabled models
        assert len(models) == 2
        ids = {m.id for m in models}
        assert ids == {"m1", "m3"}

    @pytest.mark.asyncio
    async def test_list_models_respects_access(self):
        entries = [
            ModelEntry(id="m1", backend_id="gpu-1", backend_model="m1"),
            ModelEntry(id="m2", backend_id="gpu-1", backend_model="m2"),
        ]
        svc, _, _ = self._make_service(entries)
        principal = StubPrincipal(allowed_models=["m1"])
        models = await svc.list_models(principal)
        assert len(models) == 1
        assert models[0].id == "m1"

    @pytest.mark.asyncio
    async def test_list_models_metadata(self):
        entries = [
            ModelEntry(id="m1", backend_id="gpu-1", backend_model="m1"),
        ]
        svc, _, _ = self._make_service(entries)
        principal = StubPrincipal()
        models = await svc.list_models(principal)
        m = models[0]
        assert m.object == "model"
        assert m.owned_by == "syn"
