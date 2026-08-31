"""M10 tests: admin control plane UI introspection endpoints.

Covers:
- /admin/ui renders safe shell
- Admin auth enforcement (no secret → rejected, inference key → rejected)
- /admin/overview returns safe aggregated data
- /admin/models returns canonical public IDs only (no GGUF paths)
- /admin/backends returns health info
- /admin/settings excludes admin secret
- Admin secret not present in UI HTML/JS
- No localStorage/sessionStorage secret persistence
- API key create flow (plaintext shown once)
- API key listing never returns full token
- Routing preview preserved
- Observability data preserved
- Backend-down state renders safely
- No prompt/response leakage in any endpoint
- Previous M9 admin/routing behavior preserved
- Errors render safely (no tracebacks)
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.backends.base import BackendHealthResult, BackendHealthState
from app.routing.backend_registry import BackendRegistry
from app.routing.model_registry import ModelEntry, ModelRegistry
from app.routing.router import RoutingService


ADMIN_HEADERS = {"X-Admin-Secret": "test-admin-secret"}


# ---- Stubs ------------------------------------------------------------------


class StubBackendM10:
    """Stub backend for M10 routing tests."""

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


# ---- Fixture: configured-mode app -------------------------------------------


@pytest.fixture
def configured_client(app):
    """Test client with configured-mode routing (stub backends).

    The lifespan runs when TestClient enters its context and sets up
    passthrough-mode routing. We swap in a configured-mode RoutingService
    *after* the lifespan has completed so the override sticks.
    """
    backend_a = StubBackendM10(reachable=True)
    backend_b = StubBackendM10(reachable=False)

    backend_registry = BackendRegistry()
    backend_registry.register("backend-a", backend_a)
    backend_registry.register("backend-b", backend_b)

    model_registry = ModelRegistry([
        ModelEntry(
            id="model-a",
            backend_id="backend-a",
            backend_model="/private/path/to/model-a-Q4.gguf",
            enabled=True,
            aliases=("alias-a",),
        ),
        ModelEntry(
            id="model-b",
            backend_id="backend-b",
            backend_model="/private/path/to/model-b-Q4.gguf",
            enabled=True,
        ),
        ModelEntry(
            id="model-c",
            backend_id="backend-a",
            backend_model="/private/path/to/model-c-Q4.gguf",
            enabled=False,
            aliases=("alias-c",),
        ),
    ])

    with TestClient(app) as c:
        app.state.router = RoutingService(
            model_registry=model_registry,
            backend_registry=backend_registry,
        )
        app.state.backend_registry = backend_registry
        yield c


@pytest.fixture
def configured_client_backend_down(app):
    """Test client where all backends are unreachable."""
    backend_a = StubBackendM10(reachable=False)
    backend_b = StubBackendM10(reachable=False)

    backend_registry = BackendRegistry()
    backend_registry.register("backend-a", backend_a)
    backend_registry.register("backend-b", backend_b)

    model_registry = ModelRegistry([
        ModelEntry(
            id="model-a",
            backend_id="backend-a",
            backend_model="/private/path/to/model-a-Q4.gguf",
            enabled=True,
            aliases=("alias-a",),
        ),
        ModelEntry(
            id="model-b",
            backend_id="backend-b",
            backend_model="/private/path/to/model-b-Q4.gguf",
            enabled=True,
        ),
    ])

    with TestClient(app) as c:
        app.state.router = RoutingService(
            model_registry=model_registry,
            backend_registry=backend_registry,
        )
        app.state.backend_registry = backend_registry
        yield c


# ---- 1. /admin/ui renders safe shell -----------------------------------------


def test_admin_ui_renders_safe_shell(client):
    """GET /admin/ui returns HTML with no admin secret embedded."""
    resp = client.get("/admin/ui")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/html; charset=utf-8"
    html = resp.text
    assert "test-admin-secret" not in html
    assert "Syn Admin Control Plane" in html
    assert "localStorage" not in html
    assert "sessionStorage" not in html


def test_admin_ui_rejects_no_secret(client):
    """GET /admin/ui without admin secret still returns 200 (UI shell is public)."""
    resp = client.get("/admin/ui")
    assert resp.status_code == 200


# ---- 2. Admin auth required for data endpoints ------------------------------


def test_admin_api_requires_secret(client):
    """Admin data API without secret returns 401."""
    resp = client.get("/admin/overview")
    assert resp.status_code == 401


def test_admin_api_rejects_wrong_secret(client):
    """Admin data API with wrong secret returns 401."""
    resp = client.get("/admin/overview", headers={"X-Admin-Secret": "wrong"})
    assert resp.status_code == 401


def test_admin_api_accepts_correct_secret(client):
    """Admin data API with correct secret returns 200."""
    resp = client.get("/admin/overview", headers=ADMIN_HEADERS)
    assert resp.status_code == 200


# ---- 3. Inference key cannot access admin API --------------------------------


def test_inference_key_cannot_administer(auth_env):
    """An /v1/* API key must NOT be valid for /admin/*."""
    resp = auth_env["client"].get(
        "/admin/overview",
        headers={"Authorization": f"Bearer {auth_env['token']}"},
    )
    assert resp.status_code == 401


def test_inference_key_cannot_access_models(auth_env):
    resp = auth_env["client"].get(
        "/admin/models",
        headers={"Authorization": f"Bearer {auth_env['token']}"},
    )
    assert resp.status_code == 401


def test_inference_key_cannot_access_backends(auth_env):
    resp = auth_env["client"].get(
        "/admin/backends",
        headers={"Authorization": f"Bearer {auth_env['token']}"},
    )
    assert resp.status_code == 401


def test_inference_key_cannot_access_settings(auth_env):
    resp = auth_env["client"].get(
        "/admin/settings",
        headers={"Authorization": f"Bearer {auth_env['token']}"},
    )
    assert resp.status_code == 401


# ---- 4. /admin/overview returns safe data ------------------------------------


def test_overview_returns_safe_data(client):
    """GET /admin/overview returns expected fields."""
    resp = client.get("/admin/overview", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    assert "syn_healthy" in data
    assert "routing_configured" in data
    assert "routing_mode" in data
    assert "admission" in data
    assert "backends" in data
    assert "requests" in data
    assert "tokens" in data
    assert "latency_ms" in data
    assert "ttft_ms" in data

    assert "admin_secret" not in json.dumps(data)
    assert "test-admin-secret" not in json.dumps(data)


def test_overview_in_configured_mode(configured_client):
    """Overview reports configured mode correctly."""
    resp = configured_client.get("/admin/overview", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["routing_configured"] is True
    assert data["routing_mode"] == "configured"


def test_overview_in_passthrough_mode(client):
    """Overview reports passthrough mode in default test config."""
    resp = client.get("/admin/overview", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["routing_configured"] is False
    assert data["routing_mode"] == "passthrough"


def test_overview_admission_data(client):
    """Overview includes admission state."""
    resp = client.get("/admin/overview", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "active" in data["admission"]
    assert "queued" in data["admission"]


def test_overview_request_counts(client):
    """Overview includes request outcome counts."""
    resp = client.get("/admin/overview", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "completed" in data["requests"]
    assert "failed" in data["requests"]
    assert "cancelled" in data["requests"]
    assert "rejected" in data["requests"]


def test_overview_token_counts(client):
    """Overview includes token counts."""
    resp = client.get("/admin/overview", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "prompt" in data["tokens"]
    assert "completion" in data["tokens"]
    assert "total" in data["tokens"]


def test_overview_latency_data(client):
    """Overview includes latency stats."""
    resp = client.get("/admin/overview", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "latency_ms" in data
    assert "ttft_ms" in data


# ---- 5. /admin/models returns canonical IDs only -----------------------------


def test_models_in_configured_mode(configured_client):
    """GET /admin/models returns canonical model IDs in configured mode."""
    resp = configured_client.get("/admin/models", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    assert data["configured"] is True
    assert len(data["models"]) == 3

    model_ids = {m["id"] for m in data["models"]}
    assert model_ids == {"model-a", "model-b", "model-c"}


def test_models_no_backend_native_paths(configured_client):
    """Backend-native GGUF paths must NOT be exposed."""
    resp = configured_client.get("/admin/models", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    for model in data["models"]:
        assert "backend_model" not in model
        assert "/private/" not in json.dumps(model)
        assert ".gguf" not in json.dumps(model).lower()


def test_models_in_passthrough_mode(client):
    """In passthrough mode, /admin/models returns empty list."""
    resp = client.get("/admin/models", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is False
    assert data["models"] == []


def test_models_expose_only_safe_fields(configured_client):
    """Models endpoint only exposes id, backend_id, enabled, aliases."""
    resp = configured_client.get("/admin/models", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    for model in data["models"]:
        assert set(model.keys()) == {"id", "backend_id", "enabled", "aliases"}


def test_models_aliases(configured_client):
    """Aliases are returned as a list."""
    resp = configured_client.get("/admin/models", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    models_by_id = {m["id"]: m for m in data["models"]}
    assert models_by_id["model-a"]["aliases"] == ["alias-a"]
    assert models_by_id["model-b"]["aliases"] == []
    assert models_by_id["model-c"]["aliases"] == ["alias-c"]


def test_models_enabled_flag(configured_client):
    """Model enabled/disabled flag is returned correctly."""
    resp = configured_client.get("/admin/models", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    models_by_id = {m["id"]: m for m in data["models"]}
    assert models_by_id["model-a"]["enabled"] is True
    assert models_by_id["model-c"]["enabled"] is False


# ---- 6. No backend GGUF path leakage -----------------------------------------


def test_no_gguf_paths_in_any_admin_endpoint(configured_client):
    """No admin endpoint should expose GGUF or private paths."""
    for ep in ["/admin/overview", "/admin/models", "/admin/backends", "/admin/settings"]:
        resp = configured_client.get(ep, headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        body = resp.text
        assert "/private/" not in body
        assert ".gguf" not in body.lower()


# ---- 7. /admin/backends returns health ---------------------------------------


def test_backends_in_configured_mode(configured_client):
    """GET /admin/backends returns backend health info."""
    resp = configured_client.get("/admin/backends", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    assert data["configured"] is True
    assert len(data["backends"]) == 2

    backend_ids = {b["id"] for b in data["backends"]}
    assert backend_ids == {"backend-a", "backend-b"}


def test_backends_expose_only_safe_fields(configured_client):
    """Backends endpoint only exposes id, type, reachable, state, reason."""
    resp = configured_client.get("/admin/backends", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    for backend in data["backends"]:
        assert set(backend.keys()) == {"id", "type", "reachable", "state", "reason"}


def test_backends_health_states(configured_client):
    """Backend health states are correctly reported."""
    resp = configured_client.get("/admin/backends", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    by_id = {b["id"]: b for b in data["backends"]}
    assert by_id["backend-a"]["reachable"] is True
    assert by_id["backend-a"]["state"] == "reachable"
    assert by_id["backend-b"]["reachable"] is False
    assert by_id["backend-b"]["state"] == "unreachable"


def test_backends_in_passthrough_mode(client):
    """In passthrough mode, /admin/backends returns empty list."""
    resp = client.get("/admin/backends", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is False
    assert data["backends"] == []


def test_backends_in_overview(configured_client):
    """Overview endpoint includes backend list."""
    resp = configured_client.get("/admin/overview", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["backends"]) == 2


# ---- 8. /admin/settings excludes admin secret --------------------------------


def test_settings_excludes_admin_secret(client):
    """GET /admin/settings must NOT return admin_secret."""
    resp = client.get("/admin/settings", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    body = json.dumps(data)
    assert "test-admin-secret" not in body
    assert "admin_secret" not in body


def test_settings_returns_safe_fields(client):
    """GET /admin/settings returns expected safe fields."""
    resp = client.get("/admin/settings", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    assert "cors_allowed_origins" in data
    assert "queue_timeout_seconds" in data
    assert "max_active_requests" in data
    assert "max_queued_requests" in data
    assert "request_size_limit_bytes" in data
    assert "admin_auth_configured" in data
    assert "routing_file_path" in data


def test_settings_no_full_paths(configured_client):
    """Settings should not expose full filesystem paths."""
    resp = configured_client.get("/admin/settings", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    path = data.get("routing_file_path")
    if path:
        assert "/" not in path
        assert "\\" not in path


def test_settings_in_configured_mode(configured_client):
    """Settings endpoint works in configured mode."""
    resp = configured_client.get("/admin/settings", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    assert data["admin_auth_configured"] is True
    assert isinstance(data["cors_allowed_origins"], list)
    assert data["max_active_requests"] > 0
    assert data["max_queued_requests"] > 0
    assert data["queue_timeout_seconds"] > 0
    assert data["request_size_limit_bytes"] > 0


# ---- 9. Admin secret not in UI HTML ------------------------------------------


def test_admin_secret_not_in_ui_html(client):
    """Admin secret must not appear in /admin/ui HTML."""
    resp = client.get("/admin/ui")
    assert resp.status_code == 200
    assert "test-admin-secret" not in resp.text


# ---- 10. No localStorage/sessionStorage for secret ---------------------------


def test_no_secret_persistence_in_js(client):
    """JS must not use localStorage or sessionStorage for secret."""
    resp = client.get("/admin/ui")
    assert resp.status_code == 200
    html = resp.text
    assert "localStorage" not in html
    assert "sessionStorage" not in html
    assert "test-admin-secret" not in html


# ---- 11. API key create flow (plaintext shown once) --------------------------


def test_api_key_create_returns_plaintext_once(client):
    """API key creation returns plaintext key exactly once."""
    user_resp = client.post(
        "/admin/users",
        json={"name": "m10-test-user"},
        headers=ADMIN_HEADERS,
    )
    assert user_resp.status_code == 201
    user_id = user_resp.json()["id"]

    client_resp = client.post(
        "/admin/clients",
        json={"user_id": user_id, "name": "m10-test-client"},
        headers=ADMIN_HEADERS,
    )
    assert client_resp.status_code == 201
    client_id = client_resp.json()["id"]

    key_resp = client.post(
        "/admin/api-keys",
        json={"client_id": client_id, "name": "m10-test-key"},
        headers=ADMIN_HEADERS,
    )
    assert key_resp.status_code == 201
    data = key_resp.json()
    assert "key" in data
    assert data["key"].startswith("syn_live_")
    assert "key_prefix" in data


def test_api_key_create_shows_key_prefix_only_on_list(client):
    """API key listing returns key_prefix, not the full key."""
    user_resp = client.post(
        "/admin/users",
        json={"name": "m10-test-user2"},
        headers=ADMIN_HEADERS,
    )
    user_id = user_resp.json()["id"]

    client_resp = client.post(
        "/admin/clients",
        json={"user_id": user_id, "name": "m10-test-client2"},
        headers=ADMIN_HEADERS,
    )
    client_id = client_resp.json()["id"]

    key_resp = client.post(
        "/admin/api-keys",
        json={"client_id": client_id, "name": "m10-test-key2"},
        headers=ADMIN_HEADERS,
    )
    full_key = key_resp.json()["key"]

    list_resp = client.get(
        "/admin/api-keys",
        headers=ADMIN_HEADERS,
    )
    assert list_resp.status_code == 200
    items = list_resp.json()

    for item in items:
        assert "key" not in item
        assert "key_hash" not in item
        assert full_key not in json.dumps(item)


# ---- 12. List endpoint never returns full key --------------------------------


def test_api_key_list_never_returns_full_token(client):
    """API key listing never returns the full token."""
    user_resp = client.post(
        "/admin/users",
        json={"name": "m10-full-key-test"},
        headers=ADMIN_HEADERS,
    )
    user_id = user_resp.json()["id"]

    client_resp = client.post(
        "/admin/clients",
        json={"user_id": user_id, "name": "m10-client-full-key"},
        headers=ADMIN_HEADERS,
    )
    client_id = client_resp.json()["id"]

    create_resp = client.post(
        "/admin/api-keys",
        json={"client_id": client_id, "name": "m10-key-full"},
        headers=ADMIN_HEADERS,
    )
    full_token = create_resp.json()["key"]

    list_resp = client.get("/admin/api-keys", headers=ADMIN_HEADERS)
    items = list_resp.json()
    assert len(items) >= 1
    for item in items:
        assert full_token not in json.dumps(item)


# ---- 13. Routing preview still works -------------------------------------------


def test_routing_preview_still_works_in_configured_mode(configured_client):
    """Routing preview must still work in configured mode."""
    resp = configured_client.post(
        "/admin/routing/preview",
        json={"model": "model-a"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["requested_model"] == "model-a"
    assert data["canonical_model"] == "model-a"
    assert data["backend_id"] == "backend-a"


def test_routing_preview_with_alias(configured_client):
    """Routing preview resolves aliases correctly."""
    resp = configured_client.post(
        "/admin/routing/preview",
        json={"model": "alias-a"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["canonical_model"] == "model-a"
    assert data["backend_id"] == "backend-a"


def test_routing_preview_unknown_model_returns_error(configured_client):
    """Routing preview for unknown model returns an error (not 200)."""
    resp = configured_client.post(
        "/admin/routing/preview",
        json={"model": "nonexistent-model"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code != 200


# ---- 14. Observability data still works --------------------------------------


def test_observability_summary_still_works(client):
    """Observability /summary endpoint must still work."""
    resp = client.get(
        "/admin/observability/summary",
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "requests" in data
    assert "tokens" in data
    assert "latency" in data
    assert "active" in data
    assert "queued" in data


def test_observability_recent_still_works(client):
    """Observability /recent endpoint must still work."""
    resp = client.get(
        "/admin/observability/recent",
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


def test_observability_latency_still_works(client):
    """Observability /latency endpoint must still work."""
    resp = client.get(
        "/admin/observability/latency",
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200


# ---- 15. Backend-down state renders safely ------------------------------------


def test_backend_down_state_is_safe(configured_client_backend_down):
    """When all backends are down, /admin/backends still returns safely."""
    resp = configured_client_backend_down.get(
        "/admin/backends",
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is True

    by_id = {b["id"]: b for b in data["backends"]}
    for b in by_id.values():
        assert b["reachable"] is False
        assert b["state"] == "unreachable"

    body = resp.text
    assert "Traceback" not in body
    assert "Exception" not in body


def test_overview_backend_down_safe(configured_client_backend_down):
    """Overview endpoint handles backend-down gracefully."""
    resp = configured_client_backend_down.get(
        "/admin/overview",
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "backends" in data
    assert isinstance(data["backends"], list)


# ---- 16. No prompt/response leakage -------------------------------------------


def test_no_prompt_leakage_in_models(configured_client):
    """Models endpoint must not contain any model content."""
    resp = configured_client.get("/admin/models", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    body = resp.text.lower()
    assert ".gguf" not in body


def test_no_prompt_leakage_in_backends(configured_client):
    """Backends endpoint must not contain any backend content."""
    resp = configured_client.get("/admin/backends", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    body = resp.text.lower()
    assert ".gguf" not in body
    assert "/private/" not in body


def test_no_secret_in_any_admin_endpoint(configured_client):
    """No admin endpoint should contain the admin secret."""
    for ep in ["/admin/overview", "/admin/models", "/admin/backends", "/admin/settings"]:
        resp = configured_client.get(ep, headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert "test-admin-secret" not in resp.text


# ---- 17. Previous M9 behavior preserved ---------------------------------------


def test_status_endpoint_still_works(client):
    """GET /admin/status must still return operational status."""
    resp = client.get("/admin/status", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "admission" in data
    assert "routing" in data


def test_usage_endpoint_still_works(client):
    """GET /admin/usage must still work."""
    resp = client.get("/admin/usage", headers=ADMIN_HEADERS)
    assert resp.status_code == 200


def test_dashboard_endpoint_still_works(client):
    """GET /admin/dashboard must still return HTML."""
    resp = client.get("/admin/dashboard", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/html; charset=utf-8"


def test_metrics_endpoint_still_works(client):
    """GET /admin/metrics must still return Prometheus text."""
    resp = client.get("/admin/metrics", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert "syn_requests_total" in resp.text


def test_users_endpoints_still_work(client):
    """Users CRUD must still work."""
    resp = client.post(
        "/admin/users",
        json={"name": "m10-regression-user"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 201

    resp = client.get("/admin/users", headers=ADMIN_HEADERS)
    assert resp.status_code == 200


def test_clients_endpoints_still_work(client):
    """Clients CRUD must still work."""
    user_resp = client.post(
        "/admin/users",
        json={"name": "m10-regression-user2"},
        headers=ADMIN_HEADERS,
    )
    user_id = user_resp.json()["id"]

    resp = client.post(
        "/admin/clients",
        json={"user_id": user_id, "name": "m10-regression-client"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 201

    resp = client.get("/admin/clients", headers=ADMIN_HEADERS)
    assert resp.status_code == 200


# ---- 18. Errors render safely (no tracebacks) ----------------------------------


def test_overview_error_safe(configured_client):
    """Overview endpoint handles errors without leaking tracebacks."""
    resp = configured_client.get("/admin/overview", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert "Traceback" not in resp.text
    assert "Exception" not in resp.text


def test_models_error_safe(client):
    """Models endpoint handles passthrough mode without errors."""
    resp = client.get("/admin/models", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert "Traceback" not in resp.text


def test_backends_error_safe(client):
    """Backends endpoint handles passthrough mode without errors."""
    resp = client.get("/admin/backends", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert "Traceback" not in resp.text


def test_settings_error_safe(client):
    """Settings endpoint handles errors safely."""
    resp = client.get("/admin/settings", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert "Traceback" not in resp.text


# ---- 19. Admin UI serves without auth -----------------------------------------


def test_admin_ui_serves_shell_without_secret(client):
    """UI shell is accessible without admin secret (auth is JS-side)."""
    resp = client.get("/admin/ui")
    assert resp.status_code == 200
    assert "Syn Admin" in resp.text


def test_admin_ui_contains_all_sections(client):
    """UI shell contains all expected sections."""
    resp = client.get("/admin/ui")
    assert resp.status_code == 200
    html = resp.text
    for section in ["overview", "users", "clients", "api-keys", "models", "backends", "routing", "usage", "observability", "settings"]:
        assert f'id="{section}"' in html