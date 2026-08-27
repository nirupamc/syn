"""M4 integration tests: admission control in /v1/chat/completions.

These use a controlled fake backend (via httpx.MockTransport) whose
completion is blocked/released with asyncio.Event to make concurrency
tests deterministic.
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.backends.llama_cpp import LlamaCppBackend
from app.config import Settings
from app.core.admission import AdmissionController
from app.main import create_app
from app.services import admin as admin_service


def _build_app_and_client(
    tmp_path,
    handler,
    *,
    max_active: int = 10,
    max_queue: int = 100,
    queue_timeout: float = 30.0,
):
    """Create an app with a mock backend and a configured admission controller."""
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
        max_active_requests=max_active,
        max_queue_size=max_queue,
        queue_timeout_seconds=queue_timeout,
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

    @asynccontextmanager
    async def test_lifespan(fastapi_app):
        db = Database(settings.database_url)
        db.connect()
        import app.models  # noqa: F401
        from app.db.base import Base

        Base.metadata.create_all(bind=db.engine)
        fastapi_app.state.database = db
        fastapi_app.state.admission = AdmissionController(
            max_active_requests=max_active,
            max_queue_size=max_queue,
            queue_timeout_seconds=queue_timeout,
        )
        await backend.open()
        fastapi_app.state.backend = backend
        try:
            yield
        finally:
            await backend.close()
            db.dispose()

    app.router.lifespan_context = test_lifespan

    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()

    # Create test credentials
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


def _models_handler():
    """Returns a handler that responds to /v1/models."""
    def handler(request):
        if str(request.url).endswith("/v1/models"):
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {
                            "id": "test-model",
                            "object": "model",
                            "owned_by": "llamacpp",
                            "created": 1,
                        }
                    ],
                },
            )
        return httpx.Response(404)
    return handler


def _completion_response():
    return {
        "id": "cmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        },
    }


# ---- Auth/policy before admission ------------------------------------------


def test_missing_auth_does_not_consume_capacity(tmp_path):
    """Unauthenticated requests must not occupy queue/active slots."""
    release = asyncio.Event()
    backend_calls = 0

    def handler(request):
        nonlocal backend_calls
        if str(request.url).endswith("/v1/models"):
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {
                            "id": "test-model",
                            "object": "model",
                            "owned_by": "llamacpp",
                            "created": 1,
                        }
                    ],
                },
            )
        if str(request.url).endswith("/v1/chat/completions"):
            backend_calls += 1
            release.wait()  # block forever (test ends before this)
            return httpx.Response(200, json=_completion_response())
        return httpx.Response(404)

    client, backend, _ = _build_app_and_client(
        tmp_path, handler, max_active=1, max_queue=1, queue_timeout=30.0
    )
    try:
        # Send an unauthenticated request
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 401
        # Backend should not have been called
        assert backend_calls == 0
        # Admission status should show nothing active
        status_resp = client.get(
            "/admin/status", headers={"X-Admin-Secret": "test-admin-secret"}
        )
        assert status_resp.status_code == 200
        adm = status_resp.json()["admission"]
        assert adm["active"] == 0
        assert adm["queued"] == 0
    finally:
        release.set()
        client.__exit__(None, None, None)
        asyncio.run(backend.close())


def test_invalid_model_does_not_consume_capacity(tmp_path):
    """A request for a non-existent model must not occupy queue/active slots."""
    def handler(request):
        if str(request.url).endswith("/v1/models"):
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {
                            "id": "test-model",
                            "object": "model",
                            "owned_by": "llamacpp",
                            "created": 1,
                        }
                    ],
                },
            )
        return httpx.Response(404)

    client, backend, auth_headers = _build_app_and_client(
        tmp_path, handler, max_active=1, max_queue=1, queue_timeout=30.0
    )
    try:
        resp = client.post(
            "/v1/chat/completions",
            headers=auth_headers,
            json={
                "model": "nonexistent-model",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert resp.status_code == 404
        # Admission status should show nothing active
        status_resp = client.get(
            "/admin/status", headers={"X-Admin-Secret": "test-admin-secret"}
        )
        adm = status_resp.json()["admission"]
        assert adm["active"] == 0
        assert adm["queued"] == 0
    finally:
        client.__exit__(None, None, None)
        asyncio.run(backend.close())


# ---- Status endpoint -------------------------------------------------------


def test_status_endpoint_reports_admission_state(tmp_path):
    def handler(request):
        if str(request.url).endswith("/v1/models"):
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {
                            "id": "test-model",
                            "object": "model",
                            "owned_by": "llamacpp",
                            "created": 1,
                        }
                    ],
                },
            )
        return httpx.Response(404)

    client, backend, _ = _build_app_and_client(
        tmp_path, handler, max_active=2, max_queue=8, queue_timeout=30.0
    )
    try:
        resp = client.get(
            "/admin/status", headers={"X-Admin-Secret": "test-admin-secret"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "admission" in data
        adm = data["admission"]
        assert adm["active"] == 0
        assert adm["max_active"] == 2
        assert adm["queued"] == 0
        assert adm["max_queue"] == 8
        assert adm["queue_timeout_seconds"] == 30.0
    finally:
        client.__exit__(None, None, None)
        asyncio.run(backend.close())


def test_status_endpoint_requires_admin_auth(tmp_path):
    """The status endpoint is on /admin/* and requires admin auth."""
    def handler(request):
        if str(request.url).endswith("/v1/models"):
            return httpx.Response(200, json={"object": "list", "data": []})
        return httpx.Response(404)

    client, backend, _ = _build_app_and_client(
        tmp_path, handler, max_active=1, max_queue=1, queue_timeout=1.0
    )
    try:
        # No auth
        resp = client.get("/admin/status")
        assert resp.status_code == 401
    finally:
        client.__exit__(None, None, None)
        asyncio.run(backend.close())


# ---- Direct admission controller test at HTTP boundary --------------------


def test_admission_controller_in_app_state(tmp_path):
    """The app should have an AdmissionController on app.state."""
    def handler(request):
        if str(request.url).endswith("/v1/models"):
            return httpx.Response(200, json={"object": "list", "data": []})
        return httpx.Response(404)

    client, backend, _ = _build_app_and_client(
        tmp_path, handler, max_active=3, max_queue=5, queue_timeout=10.0
    )
    try:
        admission = client.app.state.admission
        assert admission is not None
        assert admission.max_active == 3
        assert admission.max_queue == 5
        assert admission.queue_timeout_seconds == 10.0
    finally:
        client.__exit__(None, None, None)
        asyncio.run(backend.close())


# ---- Config validation -----------------------------------------------------


def test_config_rejects_zero_max_active():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(
            app_name="Syn",
            app_version="0.1.0",
            environment="testing",
            host="127.0.0.1",
            port=8001,
            database_url="sqlite:///:memory:",
            log_level="INFO",
            backend_type="llama_cpp",
            backend_base_url="http://127.0.0.1:8080",
            max_active_requests=0,
        )


def test_config_rejects_negative_max_queue():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(
            app_name="Syn",
            app_version="0.1.0",
            environment="testing",
            host="127.0.0.1",
            port=8001,
            database_url="sqlite:///:memory:",
            log_level="INFO",
            backend_type="llama_cpp",
            backend_base_url="http://127.0.0.1:8080",
            max_queue_size=-1,
        )


def test_config_rejects_zero_queue_timeout():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(
            app_name="Syn",
            app_version="0.1.0",
            environment="testing",
            host="127.0.0.1",
            port=8001,
            database_url="sqlite:///:memory:",
            log_level="INFO",
            backend_type="llama_cpp",
            backend_base_url="http://127.0.0.1:8080",
            queue_timeout_seconds=0,
        )


def test_config_accepts_valid_values():
    s = Settings(
        app_name="Syn",
        app_version="0.1.0",
        environment="testing",
        host="127.0.0.1",
        port=8001,
        database_url="sqlite:///:memory:",
        log_level="INFO",
        backend_type="llama_cpp",
        backend_base_url="http://127.0.0.1:8080",
        max_active_requests=4,
        max_queue_size=16,
        queue_timeout_seconds=60.0,
    )
    assert s.max_active_requests == 4
    assert s.max_queue_size == 16
    assert s.queue_timeout_seconds == 60.0
