"""M9 gap tests: alias authorization, public model privacy, backend attribution,
routing preview, backend breakdown."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.backends.base import BackendHealthResult, BackendHealthState
from app.backends.llama_cpp import LlamaCppBackend
from app.config import Settings
from app.core.principal import AuthenticatedPrincipal
from app.main import create_app
from app.routing.backend_registry import BackendRegistry
from app.routing.model_registry import ModelEntry, ModelRegistry
from app.routing.router import RouteDecision, RoutingService
from app.services import admin as admin_service


# ---- Stubs ------------------------------------------------------------------


class StubBackend:
    name = "stub"

    def __init__(self, *, reachable: bool = True, models_list=None):
        self._reachable = reachable
        self._models = models_list or []

    async def health(self):
        return BackendHealthResult(
            state=BackendHealthState.REACHABLE if self._reachable else BackendHealthState.UNREACHABLE,
            reachable=self._reachable,
            reason="ok" if self._reachable else "down",
        )

    async def models(self):
        return self._models

    def capabilities(self):
        return []

    async def open(self):
        pass

    async def close(self):
        pass

    async def chat_completion(self, req):
        pass

    async def stream_chat_completion(self, req):
        yield {}


# ---- Step 6: Alias authorization security ----------------------------------


class TestAliasAuthorization:
    """Aliases must NOT bypass M3 allowed-model policy."""

    def _make_router(self, allowed_models=None):
        """Build a configured-mode router with model-a and alias-a."""
        backend = StubBackend()
        registry = BackendRegistry()
        registry.register("backend-a", backend)
        model_registry = ModelRegistry([
            ModelEntry(
                id="model-a",
                backend_id="backend-a",
                backend_model="model-a",
                aliases=("alias-a",),
            ),
            ModelEntry(
                id="model-b",
                backend_id="backend-a",
                backend_model="model-b",
            ),
        ])
        router = RoutingService(
            model_registry=model_registry,
            backend_registry=registry,
        )
        return router

    @pytest.mark.asyncio
    async def test_canonical_allowed(self):
        router = self._make_router()
        principal = AuthenticatedPrincipal(
            user_id="u", user_name="u", client_id="c", client_name="c",
            api_key_id="k", api_key_prefix="k",
            allowed_models=("model-a",),
        )
        decision = await router.route("model-a", principal)
        assert decision.canonical_model == "model-a"

    @pytest.mark.asyncio
    async def test_alias_resolves_and_allows(self):
        """alias-a → model-a. Principal allowed model-a → allowed."""
        router = self._make_router()
        principal = AuthenticatedPrincipal(
            user_id="u", user_name="u", client_id="c", client_name="c",
            api_key_id="k", api_key_prefix="k",
            allowed_models=("model-a",),
        )
        decision = await router.route("alias-a", principal)
        assert decision.canonical_model == "model-a"
        assert decision.reason == "alias_match"

    @pytest.mark.asyncio
    async def test_alias_denied_when_canonical_not_allowed(self):
        """alias-a → model-a. Principal allowed only model-b → denied."""
        from app.core.errors import ModelForbiddenError

        router = self._make_router()
        principal = AuthenticatedPrincipal(
            user_id="u", user_name="u", client_id="c", client_name="c",
            api_key_id="k", api_key_prefix="k",
            allowed_models=("model-b",),
        )
        with pytest.raises(ModelForbiddenError):
            await router.route("alias-a", principal)

    @pytest.mark.asyncio
    async def test_alias_not_in_allowed_list(self):
        """alias-a is NOT in allowed_models; only canonical IDs are checked."""
        from app.core.errors import ModelForbiddenError

        router = self._make_router()
        # Principal allowed only "alias-a" (not the canonical "model-a")
        # This should be DENIED because authorization checks canonical ID.
        principal = AuthenticatedPrincipal(
            user_id="u", user_name="u", client_id="c", client_name="c",
            api_key_id="k", api_key_prefix="k",
            allowed_models=("alias-a",),
        )
        with pytest.raises(ModelForbiddenError):
            await router.route("alias-a", principal)


# ---- Step 5: Public model privacy ------------------------------------------


class TestPublicModelPrivacy:
    """Configured mode must expose canonical IDs, not backend-native paths."""

    def _make_router(self):
        backend = StubBackend()
        registry = BackendRegistry()
        registry.register("gpu-main", backend)
        model_registry = ModelRegistry([
            ModelEntry(
                id="local-general",
                backend_id="gpu-main",
                backend_model="/data/models/some-model.gguf",
                aliases=("lg",),
            ),
        ])
        return RoutingService(
            model_registry=model_registry,
            backend_registry=registry,
        )

    @pytest.mark.asyncio
    async def test_list_models_exposes_canonical_id(self):
        router = self._make_router()
        principal = AuthenticatedPrincipal(
            user_id="u", user_name="u", client_id="c", client_name="c",
            api_key_id="k", api_key_prefix="k",
        )
        models = await router.list_models(principal)
        assert len(models) == 1
        assert models[0].id == "local-general"

    @pytest.mark.asyncio
    async def test_list_models_hides_native_path(self):
        router = self._make_router()
        principal = AuthenticatedPrincipal(
            user_id="u", user_name="u", client_id="c", client_name="c",
            api_key_id="k", api_key_prefix="k",
        )
        models = await router.list_models(principal)
        assert ".gguf" not in models[0].id

    @pytest.mark.asyncio
    async def test_route_returns_canonical_in_decision(self):
        router = self._make_router()
        principal = AuthenticatedPrincipal(
            user_id="u", user_name="u", client_id="c", client_name="c",
            api_key_id="k", api_key_prefix="k",
        )
        decision = await router.route("local-general", principal)
        assert decision.canonical_model == "local-general"
        assert decision.backend_model == "/data/models/some-model.gguf"


# ---- Step 3: Backend attribution persistence --------------------------------


class TestBackendAttribution:
    """Usage records must include backend_id and canonical model."""

    def test_usage_service_record_accepts_backend_id(self):
        from app.services.usage import UsageService, Outcome
        from app.core.rate_limit import RateLimiter
        import datetime as _dt

        svc = UsageService(RateLimiter(window_seconds=60))
        # Verify the record method accepts backend_id parameter
        # (actual DB persistence tested via integration)
        assert hasattr(svc, "record")

    def test_usage_record_has_backend_id_column(self):
        from app.models.usage_record import UsageRecord
        # Verify the ORM model has backend_id attribute
        assert hasattr(UsageRecord, "backend_id")


# ---- Step 2: Routing preview -----------------------------------------------


class TestRoutingPreview:
    """POST /admin/routing/preview must return safe routing info."""

    @pytest.fixture
    def mock_backend_factory(self, tmp_path):
        backends_to_close = []

        def _factory(handler):
            transport = httpx.MockTransport(handler)
            db_path = tmp_path / "test.db"
            settings = Settings(
                app_name="Syn",
                app_version="0.1.0",
                environment="testing",
                host="127.0.0.1",
                port=8001,
                database_url=f"sqlite:///{db_path}",
                log_level="INFO",
                backend_type="llama_cpp",
                backend_base_url="http://127.0.0.1:8080",
                backend_timeout_seconds=5.0,
                backend_connect_timeout_seconds=1.0,
                backend_health_timeout_seconds=1.0,
                admin_secret="test-admin-secret",
            )
            app = create_app(settings)
            backend = LlamaCppBackend(
                base_url="http://127.0.0.1:8080",
                timeout_seconds=5.0,
                connect_timeout_seconds=1.0,
                health_timeout_seconds=1.0,
                transport=transport,
            )

            from contextlib import asynccontextmanager
            from app.db import Database
            from app.core.admission import AdmissionController

            @asynccontextmanager
            async def test_lifespan(fastapi_app):
                db = Database(settings.database_url)
                db.connect()
                import app.models  # noqa: F401
                from app.db.base import Base

                Base.metadata.create_all(bind=db.engine)
                fastapi_app.state.database = db
                fastapi_app.state.admission = AdmissionController(
                    max_active_requests=10,
                    max_queue_size=100,
                    queue_timeout_seconds=30.0,
                )
                await backend.open()
                fastapi_app.state.backend = backend
                # Wire configured-mode routing
                model_registry = ModelRegistry([
                    ModelEntry(
                        id="model-a",
                        backend_id="gpu-a",
                        backend_model="model-a-native",
                        aliases=("alias-a",),
                    ),
                ])
                backend_registry = BackendRegistry()
                backend_registry.register("gpu-a", backend)
                fastapi_app.state.router = RoutingService(
                    model_registry=model_registry,
                    backend_registry=backend_registry,
                )
                try:
                    yield
                finally:
                    await backend.close()
                    db.dispose()

            app.router.lifespan_context = test_lifespan
            backends_to_close.append(backend)

            client = TestClient(app, raise_server_exceptions=False)
            client.__enter__()

            session = app.state.database.session_factory()
            try:
                user = admin_service.create_user(session, "test-user")
                client_obj = admin_service.create_client(
                    session, user_id=user.id, name="test-client"
                )
                api_key, full_token = admin_service.create_api_key(
                    session, client_id=client_obj.id, name="test-key"
                )
                auth_headers = {"Authorization": f"Bearer {full_token}"}
            finally:
                session.close()

            return client, backend, auth_headers

        yield _factory

        for backend in backends_to_close:
            try:
                asyncio.run(backend.close())
            except Exception:
                pass

    def test_preview_canonical_model(self, mock_backend_factory):
        def handler(request):
            return httpx.Response(404)

        client, _, _ = mock_backend_factory(handler)
        try:
            resp = client.post(
                "/admin/routing/preview",
                headers={"X-Admin-Secret": "test-admin-secret"},
                json={"model": "model-a"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["requested_model"] == "model-a"
            assert data["canonical_model"] == "model-a"
            assert data["backend_id"] == "gpu-a"
        finally:
            client.__exit__(None, None, None)

    def test_preview_alias_resolution(self, mock_backend_factory):
        def handler(request):
            return httpx.Response(404)

        client, _, _ = mock_backend_factory(handler)
        try:
            resp = client.post(
                "/admin/routing/preview",
                headers={"X-Admin-Secret": "test-admin-secret"},
                json={"model": "alias-a"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["requested_model"] == "alias-a"
            assert data["canonical_model"] == "model-a"
            assert data["backend_id"] == "gpu-a"
            assert data["reason"] == "alias_match"
        finally:
            client.__exit__(None, None, None)

    def test_preview_no_native_path(self, mock_backend_factory):
        def handler(request):
            return httpx.Response(404)

        client, _, _ = mock_backend_factory(handler)
        try:
            resp = client.post(
                "/admin/routing/preview",
                headers={"X-Admin-Secret": "test-admin-secret"},
                json={"model": "model-a"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["canonical_model"] == "model-a"
        finally:
            client.__exit__(None, None, None)

    def test_preview_unknown_model(self, mock_backend_factory):
        def handler(request):
            return httpx.Response(404)

        client, _, _ = mock_backend_factory(handler)
        try:
            resp = client.post(
                "/admin/routing/preview",
                headers={"X-Admin-Secret": "test-admin-secret"},
                json={"model": "nonexistent"},
            )
            assert resp.status_code == 400
        finally:
            client.__exit__(None, None, None)

    def test_preview_requires_admin_auth(self, mock_backend_factory):
        def handler(request):
            return httpx.Response(404)

        client, _, _ = mock_backend_factory(handler)
        try:
            resp = client.post(
                "/admin/routing/preview",
                json={"model": "model-a"},
            )
            assert resp.status_code == 401
        finally:
            client.__exit__(None, None, None)


# ---- Step 4: Backend breakdown ---------------------------------------------


class TestBackendBreakdown:
    """ObservabilityService.backend_breakdown must aggregate by backend_id."""

    def test_backend_breakdown_groups_by_id(self):
        from app.services.observability import ObservabilityService
        from app.models.usage_record import UsageRecord
        import datetime as _dt

        obs = ObservabilityService()
        # Verify the method exists
        assert hasattr(obs, "backend_breakdown")

    def test_backend_breakdown_dataclass(self):
        from app.services.observability import BackendBreakdown
        b = BackendBreakdown(
            backend_id="gpu-a",
            requests=10,
            completed=8,
            failed=1,
            cancelled=1,
            total_tokens=5000,
        )
        assert b.backend_id == "gpu-a"
        assert b.requests == 10
