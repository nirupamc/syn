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
    assert "localStorage.setItem('syn-theme'" in html
    assert "localStorage.getItem('syn-theme'" in html
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
    """Models endpoint exposes safe fields including runtime detection."""
    resp = configured_client.get("/admin/models", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    expected_keys = {
        "id", "backend_id", "enabled", "aliases",
        "backend_reachable", "runtime_loaded", "runtime_model", "runtime_status",
    }
    for model in data["models"]:
        assert set(model.keys()) == expected_keys
        assert isinstance(model["backend_reachable"], bool)
        assert isinstance(model["runtime_loaded"], bool)
        assert model["runtime_model"] is None or isinstance(model["runtime_model"], str)
        assert model["runtime_status"] in {"online", "offline", "no_model", "error"}


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
    """Backends endpoint exposes safe fields including runtime models list."""
    resp = configured_client.get("/admin/backends", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    for backend in data["backends"]:
        assert set(backend.keys()) == {
            "id", "type", "reachable", "state", "reason",
            "runtime_model", "runtime_models", "server_version",
            "last_checked", "endpoint",
        }
        assert isinstance(backend["runtime_models"], list)
        for m in backend["runtime_models"]:
            assert isinstance(m, str)
        assert backend["runtime_model"] is None or isinstance(backend["runtime_model"], str)
        assert backend["server_version"] is None or isinstance(backend["server_version"], str)
        assert backend["last_checked"] is None or isinstance(backend["last_checked"], str)


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
    """Only the non-sensitive theme preference may be persisted."""
    resp = client.get("/admin/ui")
    assert resp.status_code == 200
    html = resp.text
    assert html.count("localStorage.setItem(") == 1
    assert "localStorage.setItem('syn-theme'" in html
    assert "localStorage.setItem('admin" not in html
    assert "localStorage.setItem('secret" not in html
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


# ---- 20. M10 section visibility / navigation structure --------------------------
#
# Regression guard for the "sections stack vertically" defect. The admin UI must
# have exactly 10 sibling sections inside #main, exactly one of which is active
# on load, with CSS + JS that guarantee only that one is visible at a time.


EXPECTED_SECTIONS = [
    "overview", "users", "clients", "api-keys", "models",
    "backends", "routing", "usage", "observability", "settings",
]


def _parse_html_tree(html):
    """Build a minimal DOM tree from an HTML string using the stdlib parser."""
    from html.parser import HTMLParser

    class Node:
        __slots__ = ("tag", "attrs", "children", "parent", "text")

        def __init__(self, tag, attrs=None, text=None):
            self.tag = tag
            self.attrs = dict(attrs) if attrs else {}
            self.children = []
            self.parent = None
            self.text = text

        @property
        def classes(self):
            return self.attrs.get("class", "").split()

        @property
        def is_section(self):
            return "section" in self.classes

        @property
        def is_active(self):
            return "active" in self.classes

        def add(self, child):
            child.parent = self
            self.children.append(child)

    class TreeBuilder(HTMLParser):
        void = {
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr",
        }

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.root = Node("#doc")
            self.stack = [self.root]

        def handle_starttag(self, tag, attrs):
            n = Node(tag, attrs)
            self.stack[-1].add(n)
            if tag not in self.void:
                self.stack.append(n)

        def handle_startendtag(self, tag, attrs):
            self.stack[-1].add(Node(tag, attrs))

        def handle_endtag(self, tag):
            for i in range(len(self.stack) - 1, 0, -1):
                if self.stack[i].tag == tag:
                    del self.stack[i:]
                    break

        def handle_data(self, data):
            self.stack[-1].add(Node("#text", text=data))

    b = TreeBuilder()
    b.feed(html)
    b.close()
    return b.root


def _iter_nodes(node):
    yield node
    for c in node.children:
        yield from _iter_nodes(c)


def _sections(root):
    return {
        n.attrs.get("id"): n
        for n in _iter_nodes(root)
        if n.tag != "#text" and n.is_section
    }


def _find_by_id(root, id_):
    for n in _iter_nodes(root):
        if n.tag != "#text" and n.attrs.get("id") == id_:
            return n
    return None


def _node_text(node):
    return "".join(n.text or "" for n in _iter_nodes(node) if n.tag == "#text")


def _main_node(root):
    return _find_by_id(root, "main")


def test_m10_exactly_ten_sections(client):
    """UI shell has exactly 10 .section elements, one per expected view."""
    resp = client.get("/admin/ui")
    assert resp.status_code == 200
    root = _parse_html_tree(resp.text)
    secs = _sections(root)
    assert len(secs) == 10
    for expected in EXPECTED_SECTIONS:
        assert expected in secs, f"missing section: {expected}"


def test_m10_sections_are_siblings_in_main(client):
    """All 10 sections are direct children of #main (siblings, never nested)."""
    resp = client.get("/admin/ui")
    assert resp.status_code == 200
    root = _parse_html_tree(resp.text)
    main = _main_node(root)
    assert main is not None
    secs = _sections(root)
    assert len(secs) == 10
    section_ids_in_main = [
        c.attrs.get("id")
        for c in main.children
        if c.tag != "#text" and c.is_section
    ]
    assert sorted(section_ids_in_main) == sorted(EXPECTED_SECTIONS)
    for sid, n in secs.items():
        assert n.parent is main, f"section {sid} is not a direct child of #main"


def test_m10_exactly_one_section_active_on_load(client):
    """Exactly one section is active on initial load (the overview)."""
    resp = client.get("/admin/ui")
    assert resp.status_code == 200
    root = _parse_html_tree(resp.text)
    active = [sid for sid, n in _sections(root).items() if n.is_active]
    assert len(active) == 1
    assert active[0] == "overview"


def test_m10_css_hides_inactive_sections(client):
    """CSS rule .section hides inactive sections; .section.active shows the active one."""
    import re

    resp = client.get("/admin/ui")
    assert resp.status_code == 200
    root = _parse_html_tree(resp.text)
    style = None
    for n in _iter_nodes(root):
        if n.tag == "style":
            style = _node_text(n)
    assert style is not None
    assert re.search(r"\.section\s*\{[^}]*display\s*:\s*none", style), (
        "inactive .section must be hidden (display:none)"
    )
    assert re.search(
        r"\.section\.active\s*\{[^}]*display\s*:\s*block", style
    ), "active .section must be shown (display:block)"
    assert ".section.hidden" not in style, (
        "mixed .section.hidden visibility system must not coexist with .section.active"
    )


def test_m10_nav_logic_deactivates_previous_section(client):
    """Navigation JS removes 'active' from all sections, then adds to target."""
    import re

    resp = client.get("/admin/ui")
    assert resp.status_code == 200
    root = _parse_html_tree(resp.text)
    script = None
    for n in _iter_nodes(root):
        if n.tag == "script":
            script = _node_text(n)
    assert script is not None
    # Deactivation: every .section has 'active' removed.
    assert "querySelectorAll('.section')" in script
    assert "classList.remove('active')" in script
    # Activation: target gains 'active'.
    assert "classList.add('active')" in script
    # Switchers used for the actual section visibility (not only sidebar links).
    assert re.search(r"function\s+switchSection\s*\(", script)
    # In switchSection, deactivation must happen before activation.
    m = re.search(r"function\s+switchSection\s*\(([^)]*)\)\s*\{(.*?)\}", script, re.S)
    assert m, "switchSection function not found"
    body = m.group(2)
    remove_idx = body.find("classList.remove('active')")
    add_idx = body.find("classList.add('active')")
    assert remove_idx != -1 and add_idx != -1
    assert remove_idx < add_idx, "switchSection must deactivate before activating"


def test_m10_sidebar_links_map_to_sections(client):
    """Every sidebar nav link has a data-section matching a real section id."""
    resp = client.get("/admin/ui")
    assert resp.status_code == 200
    root = _parse_html_tree(resp.text)
    links = [
        n for n in _iter_nodes(root)
        if n.tag == "a" and n.attrs.get("data-section")
    ]
    data_sections = {l.attrs["data-section"] for l in links}
    assert data_sections == set(EXPECTED_SECTIONS)
    for sid in EXPECTED_SECTIONS:
        assert _find_by_id(root, sid) is not None


def test_m10_list_containers_nested_in_their_section(client):
    """List containers (e.g. #user-list) live inside their owning section, not orphaned."""
    resp = client.get("/admin/ui")
    assert resp.status_code == 200
    root = _parse_html_tree(resp.text)

    def _nearest_section_ancestor(node):
        p = node.parent
        while p is not None and p.tag != "#doc":
            if p.is_section:
                return p
            p = p.parent
        return None

    for container, owner in [("user-list", "users"), ("client-list", "clients")]:
        el = _find_by_id(root, container)
        assert el is not None, f"missing list container #{container}"
        ancestor = _nearest_section_ancestor(el)
        assert ancestor is not None, f"#{container} is not nested in a .section"
        assert ancestor.attrs.get("id") == owner, (
            f"#{container} should be inside #{owner} section"
        )


def test_m10_no_unicode_mojibake_in_ui(client):
    """No en/em-dash characters that can mojibake; null values use N/A."""
    resp = client.get("/admin/ui")
    assert resp.status_code == 200
    html = resp.text
    assert "\u2013" not in html, "en-dash (potential mojibake) present in UI"
    assert "\u2014" not in html, "em-dash (potential mojibake) present in UI"
    assert "\u00e2\u20ac" not in html, "mojibake bytes (â€...) present in UI"
    assert html.count("N/A") >= 10


def test_m10_create_and_preview_buttons_are_styled(client):
    """Actions use the native Syn design system and custom dialogs."""
    resp = client.get("/admin/ui")
    assert resp.status_code == 200
    html = resp.text
    assert '<button type="button" id="create-user"' in html
    assert '<button type="button" id="create-client"' in html
    assert 'id="create-key"' in html
    assert 'id="preview-btn"' in html
    assert 'class="btn btn-primary"' in html
    assert '.btn-primary' in html
    assert '.btn-secondary' in html
    assert '.btn-danger' in html
    assert '.btn-ghost' in html
    assert '.btn-sm' in html
    assert 'prompt(' not in html
    assert 'confirm(' not in html
    # Modal system exists for every mutating workflow.
    assert 'modal-create-user' in html
    assert 'modal-create-client' in html
    assert 'modal-policy' in html
    assert 'modal-create-key' in html
    assert 'modal-rotate-key' in html
    assert 'modal-revoke-key' in html
    assert '<dialog id="key-plaintext-modal"' in html
    assert 'showModal()' in html
    # The retired library and its class vocabulary stay removed.
    assert 'cs16.min.css' not in html
    for legacy_class in ['cs-btn', 'cs-input', 'cs-select', 'cs-dialog', 'cs-tabs', 'cs-fieldset', 'cs-progress-bar', 'cs-tooltip']:
        assert legacy_class not in html


def test_runtime_model_no_full_path_leakage(configured_client):
    """Reachable backend exposes runtime model safely without full GGUF paths."""
    resp = configured_client.get("/admin/backends", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    for backend in data["backends"]:
        if backend["reachable"] and backend["runtime_model"]:
            assert "\\" not in backend["runtime_model"], "full path leaked"
            assert "/" not in backend["runtime_model"], "full path leaked"


def test_local_inference_in_overview(configured_client):
    """Overview exposes a local_inference summary block."""
    resp = configured_client.get("/admin/overview", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "local_inference" in data
    inf = data["local_inference"]
    assert "model" in inf
    assert "backend" in inf
    assert "type" in inf
    assert "status" in inf
    assert "endpoint" in inf


def test_local_inference_in_ui(client):
    """UI overview page renders a local inference card."""
    resp = client.get("/admin/ui")
    assert resp.status_code == 200
    html = resp.text
    assert "inf-model" in html
    assert "inf-status" in html
    assert "LOCAL INFERENCE" in html


def test_runtime_model_unreachable_backend(configured_client):
    """Unreachable backend returns runtime_model null."""
    resp = configured_client.get("/admin/backends", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    by_id = {b["id"]: b for b in data["backends"]}
    if "backend-b" in by_id:
        assert by_id["backend-b"]["reachable"] is False
        assert by_id["backend-b"]["runtime_model"] is None
        assert by_id["backend-b"]["runtime_models"] == []


# ---- Runtime detection regression tests ------------------------------------


def test_runtime_status_online_for_reachable_backend_with_model(configured_client):
    """reachable backend + real model discovered -> runtime_status=online."""
    resp = configured_client.get("/admin/models", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    by_id = {m["id"]: m for m in data["models"]}
    if "model-a" in by_id:
        m = by_id["model-a"]
        # With the configured fixture, backend-a is reachable. The stub
        # returns no models by default, so the runtime_status is
        # 'no_model' (reachable but nothing discovered). This validates
        # that we do NOT falsely report 'online' without a real model.
        assert m["backend_reachable"] is True
        assert m["runtime_status"] in {"online", "no_model"}
        if m["runtime_status"] == "online":
            assert m["runtime_loaded"] is True
            assert m["runtime_model"] is not None
        else:
            assert m["runtime_status"] == "no_model"
            assert m["runtime_loaded"] is False


def test_runtime_status_offline_for_unreachable_backend(configured_client):
    """unreachable backend -> runtime_status=offline, runtime_loaded=false."""
    resp = configured_client.get("/admin/models", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    by_id = {m["id"]: m for m in data["models"]}
    if "model-b" in by_id:
        m = by_id["model-b"]
        assert m["backend_reachable"] is False
        assert m["runtime_status"] == "offline"
        assert m["runtime_loaded"] is False
        assert m["runtime_model"] is None


def test_enabled_does_not_imply_runtime_loaded(configured_client):
    """enabled is a config flag; runtime_loaded comes from real probe."""
    resp = configured_client.get("/admin/models", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    by_id = {m["id"]: m for m in data["models"]}
    for m in data["models"]:
        # enabled is configuration; runtime_loaded is independent.
        # backend-b is enabled but offline => runtime_loaded must be false.
        if m["backend_reachable"] is False:
            assert m["runtime_loaded"] is False
            assert m["runtime_status"] == "offline"


def test_runtime_model_path_sanitized(configured_client):
    """runtime_model never contains full filesystem paths."""
    resp = configured_client.get("/admin/backends", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    for b in data["backends"]:
        for m in b.get("runtime_models", []):
            assert "\\" not in m
            assert "/" not in m
        if b.get("runtime_model"):
            assert "\\" not in b["runtime_model"]
            assert "/" not in b["runtime_model"]


def test_backends_runtime_models_empty_when_unreachable(configured_client):
    """Unreachable backend exposes empty runtime_models list, not stale data."""
    resp = configured_client.get("/admin/backends", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    by_id = {b["id"]: b for b in data["backends"]}
    if "backend-b" in by_id:
        assert by_id["backend-b"]["reachable"] is False
        assert by_id["backend-b"]["runtime_models"] == []


def test_admin_auth_required_for_runtime_endpoints(client):
    """Admin secret is still required for models/backends runtime endpoints."""
    for path in ("/admin/models", "/admin/backends", "/admin/overview"):
        resp = client.get(path)
        assert resp.status_code == 401, f"{path} should require admin auth"


@pytest.fixture
def client_with_loaded_model(app):
    """Configured-mode client where backend-a reports a real loaded model."""
    from app.backends.base import BackendModelInfo

    loaded = [
        BackendModelInfo(id="/private/path/to/loaded-Q4_K_M.gguf", object="model", owned_by="llamacpp"),
    ]
    backend_a = StubBackendM10(reachable=True, models_list=loaded)
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


def test_runtime_status_online_when_model_loaded(client_with_loaded_model):
    """When backend returns a real model, runtime_status=online and name is sanitized."""
    resp = client_with_loaded_model.get("/admin/models", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    by_id = {m["id"]: m for m in data["models"]}
    a = by_id["model-a"]
    assert a["backend_reachable"] is True
    assert a["runtime_status"] == "online"
    assert a["runtime_loaded"] is True
    # Full path must be stripped to basename.
    assert a["runtime_model"] == "loaded-Q4_K_M.gguf"
    assert "\\" not in (a["runtime_model"] or "")
    assert "/" not in (a["runtime_model"] or "")

    b = by_id["model-b"]
    assert b["backend_reachable"] is False
    assert b["runtime_status"] == "offline"
    assert b["runtime_loaded"] is False
    assert b["runtime_model"] is None


def test_backends_runtime_models_with_loaded_model(client_with_loaded_model):
    """Backends endpoint exposes the full runtime_models list when a model is loaded."""
    resp = client_with_loaded_model.get("/admin/backends", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    by_id = {b["id"]: b for b in data["backends"]}
    assert by_id["backend-a"]["reachable"] is True
    assert "loaded-Q4_K_M.gguf" in by_id["backend-a"]["runtime_models"]
    # No path leakage in the list either.
    for m in by_id["backend-a"]["runtime_models"]:
        assert "\\" not in m and "/" not in m

    assert by_id["backend-b"]["reachable"] is False
    assert by_id["backend-b"]["runtime_models"] == []


def test_fresh_refresh_changes_state(client_with_loaded_model, monkeypatch):
    """If the backend goes offline between calls, runtime_status updates without restart."""
    resp = client_with_loaded_model.get("/admin/models", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    by_id = {m["id"]: m for m in data["models"]}
    assert by_id["model-a"]["runtime_status"] == "online"

    # Simulate backend-a going down. The next call must reflect the new state.
    registry = client_with_loaded_model.app.state.backend_registry
    backend_a = registry.get("backend-a")
    backend_a._reachable = False
    backend_a._models = []

    resp2 = client_with_loaded_model.get("/admin/models", headers=ADMIN_HEADERS)
    data2 = resp2.json()
    by_id2 = {m["id"]: m for m in data2["models"]}
    a2 = by_id2["model-a"]
    assert a2["backend_reachable"] is False
    assert a2["runtime_status"] == "offline"
    assert a2["runtime_loaded"] is False
    assert a2["runtime_model"] is None


def test_no_stale_runtime_model_when_backend_unreachable(client_with_loaded_model, monkeypatch):
    """When a backend becomes unreachable, its runtime_model must be nulled, not stale."""
    resp = client_with_loaded_model.get("/admin/backends", headers=ADMIN_HEADERS)
    data = resp.json()
    by_id = {b["id"]: b for b in data["backends"]}
    assert by_id["backend-a"]["runtime_model"] == "loaded-Q4_K_M.gguf"

    # Go offline.
    registry = client_with_loaded_model.app.state.backend_registry
    backend_a = registry.get("backend-a")
    backend_a._reachable = False
    backend_a._models = []

    resp2 = client_with_loaded_model.get("/admin/backends", headers=ADMIN_HEADERS)
    data2 = resp2.json()
    by_id2 = {b["id"]: b for b in data2["backends"]}
    assert by_id2["backend-a"]["runtime_model"] is None
    assert by_id2["backend-a"]["runtime_models"] == []
    assert by_id2["backend-a"]["reachable"] is False


def test_multiple_backends_independently_detected(app):
    """Each backend's runtime state is detected independently."""
    from app.backends.base import BackendModelInfo

    backend_a = StubBackendM10(reachable=True, models_list=[
        BackendModelInfo(id="model-a-loaded.gguf", object="model", owned_by="llamacpp"),
    ])
    backend_b = StubBackendM10(reachable=True, models_list=[
        BackendModelInfo(id="model-b-loaded.gguf", object="model", owned_by="llamacpp"),
    ])

    backend_registry = BackendRegistry()
    backend_registry.register("backend-a", backend_a)
    backend_registry.register("backend-b", backend_b)

    model_registry = ModelRegistry([
        ModelEntry(id="model-a", backend_id="backend-a", backend_model="model-a-loaded.gguf", enabled=True),
        ModelEntry(id="model-b", backend_id="backend-b", backend_model="model-b-loaded.gguf", enabled=True),
    ])

    with TestClient(app) as c:
        app.state.router = RoutingService(
            model_registry=model_registry,
            backend_registry=backend_registry,
        )
        app.state.backend_registry = backend_registry
        resp = c.get("/admin/models", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        by_id = {m["id"]: m for m in data["models"]}
        assert by_id["model-a"]["runtime_status"] == "online"
        assert by_id["model-a"]["runtime_model"] == "model-a-loaded.gguf"
        assert by_id["model-b"]["runtime_status"] == "online"
        assert by_id["model-b"]["runtime_model"] == "model-b-loaded.gguf"
