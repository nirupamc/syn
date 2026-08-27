"""M2+M3 isolated tests for /v1/chat/completions endpoint.

These exercise the API route against controlled backend behavior via MockTransport.
They require NO real llama.cpp, NO GPU, and NO network.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.backends.llama_cpp import LlamaCppBackend
from app.config import Settings
from app.main import create_app
from app.services import admin as admin_service


@pytest.fixture
def mock_backend_factory(tmp_path):
    """Factory that creates a mock backend and replaces the app's backend.

    Returns a function that takes a handler and returns a (client, backend, auth_headers) tuple.
    """
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
        from app.logging import get_logger

        logger = get_logger("syn.test")

        @asynccontextmanager
        async def test_lifespan(fastapi_app):
            db = Database(settings.database_url)
            db.connect()
            import app.models  # noqa: F401
            from app.db.base import Base

            Base.metadata.create_all(bind=db.engine)
            fastapi_app.state.database = db
            # Set a permissive admission controller for tests
            fastapi_app.state.admission = AdmissionController(
                max_active_requests=10,
                max_queue_size=100,
                queue_timeout_seconds=30.0,
            )
            await backend.open()
            fastapi_app.state.backend = backend
            try:
                yield
            finally:
                await backend.close()
                db.dispose()

        app.router.lifespan_context = test_lifespan
        backends_to_close.append(backend)

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

    yield _factory

    for backend in backends_to_close:
        try:
            asyncio.run(backend.close())
        except Exception:
            pass


def _models_payload():
    return {
        "object": "list",
        "data": [
            {
                "id": "test-model",
                "object": "model",
                "owned_by": "llamacpp",
                "created": 1787840342,
            }
        ],
    }


def _chat_completion_payload(content="Hello!", model="test-model"):
    return {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "created": 1700000000,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
        },
    }


# ---- Auth required ----------------------------------------------------------


def test_chat_completion_requires_auth(mock_backend_factory):
    def handler(request):
        return httpx.Response(200, json=_models_payload())

    client, backend, _ = mock_backend_factory(handler)
    try:
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hi"}],
            },
        )
        assert resp.status_code == 401
    finally:
        client.__exit__(None, None, None)


# ---- Success cases ---------------------------------------------------------


def test_chat_completion_success(mock_backend_factory):
    def handler(request):
        if str(request.url).endswith("/v1/models"):
            return httpx.Response(200, json=_models_payload())
        if str(request.url).endswith("/v1/chat/completions"):
            body = request.read()
            import json

            req_data = json.loads(body)
            assert req_data["model"] == "test-model"
            assert req_data["stream"] is False
            return httpx.Response(200, json=_chat_completion_payload("SYN_OK"))
        return httpx.Response(404, text="not found")

    client, backend, auth_headers = mock_backend_factory(handler)
    try:
        resp = client.post(
            "/v1/chat/completions",
            headers=auth_headers,
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hi"}],
                "temperature": 0.0,
                "max_tokens": 32,
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "chat.completion"
        assert data["model"] == "test-model"
        assert len(data["choices"]) == 1
        assert data["choices"][0]["message"]["role"] == "assistant"
        assert data["choices"][0]["message"]["content"] == "SYN_OK"
        assert data["choices"][0]["finish_reason"] == "stop"
        assert data["usage"]["prompt_tokens"] == 10
        assert data["usage"]["completion_tokens"] == 2
        assert data["usage"]["total_tokens"] == 12
    finally:
        client.__exit__(None, None, None)


def test_chat_completion_with_all_optional_params(mock_backend_factory):
    def handler(request):
        if str(request.url).endswith("/v1/models"):
            return httpx.Response(200, json=_models_payload())
        if str(request.url).endswith("/v1/chat/completions"):
            body = request.read()
            import json

            req_data = json.loads(body)
            assert req_data["temperature"] == 0.7
            assert req_data["top_p"] == 0.9
            assert req_data["max_tokens"] == 100
            assert req_data["stop"] == ["END"]
            return httpx.Response(200, json=_chat_completion_payload())
        return httpx.Response(404)

    client, backend, auth_headers = mock_backend_factory(handler)
    try:
        resp = client.post(
            "/v1/chat/completions",
            headers=auth_headers,
            json={
                "model": "test-model",
                "messages": [
                    {"role": "system", "content": "You are helpful"},
                    {"role": "user", "content": "Hi"},
                ],
                "temperature": 0.7,
                "top_p": 0.9,
                "max_tokens": 100,
                "stop": ["END"],
            },
        )

        assert resp.status_code == 200
    finally:
        client.__exit__(None, None, None)


def test_chat_completion_explicit_stream_false(mock_backend_factory):
    def handler(request):
        if str(request.url).endswith("/v1/models"):
            return httpx.Response(200, json=_models_payload())
        if str(request.url).endswith("/v1/chat/completions"):
            return httpx.Response(200, json=_chat_completion_payload())
        return httpx.Response(404)

    client, backend, auth_headers = mock_backend_factory(handler)
    try:
        resp = client.post(
            "/v1/chat/completions",
            headers=auth_headers,
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": False,
            },
        )

        assert resp.status_code == 200
    finally:
        client.__exit__(None, None, None)


# ---- Validation errors -----------------------------------------------------


def test_chat_completion_empty_messages(mock_backend_factory):
    def handler(request):
        if str(request.url).endswith("/v1/models"):
            return httpx.Response(200, json=_models_payload())
        return httpx.Response(404)

    client, backend, auth_headers = mock_backend_factory(handler)
    try:
        resp = client.post(
            "/v1/chat/completions",
            headers=auth_headers,
            json={"model": "test-model", "messages": []},
        )

        assert resp.status_code == 422
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == "validation_error"
    finally:
        client.__exit__(None, None, None)


def test_chat_completion_invalid_role(mock_backend_factory):
    def handler(request):
        if str(request.url).endswith("/v1/models"):
            return httpx.Response(200, json=_models_payload())
        return httpx.Response(404)

    client, backend, auth_headers = mock_backend_factory(handler)
    try:
        resp = client.post(
            "/v1/chat/completions",
            headers=auth_headers,
            json={
                "model": "test-model",
                "messages": [{"role": "invalid_role", "content": "Hi"}],
            },
        )

        assert resp.status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_chat_completion_empty_content(mock_backend_factory):
    def handler(request):
        if str(request.url).endswith("/v1/models"):
            return httpx.Response(200, json=_models_payload())
        return httpx.Response(404)

    client, backend, auth_headers = mock_backend_factory(handler)
    try:
        resp = client.post(
            "/v1/chat/completions",
            headers=auth_headers,
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": ""}],
            },
        )

        assert resp.status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_chat_completion_invalid_temperature(mock_backend_factory):
    def handler(request):
        if str(request.url).endswith("/v1/models"):
            return httpx.Response(200, json=_models_payload())
        return httpx.Response(404)

    client, backend, auth_headers = mock_backend_factory(handler)
    try:
        resp = client.post(
            "/v1/chat/completions",
            headers=auth_headers,
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hi"}],
                "temperature": 3.0,  # > 2.0
            },
        )

        assert resp.status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_chat_completion_invalid_max_tokens(mock_backend_factory):
    def handler(request):
        if str(request.url).endswith("/v1/models"):
            return httpx.Response(200, json=_models_payload())
        return httpx.Response(404)

    client, backend, auth_headers = mock_backend_factory(handler)
    try:
        resp = client.post(
            "/v1/chat/completions",
            headers=auth_headers,
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 0,
            },
        )

        assert resp.status_code == 422
    finally:
        client.__exit__(None, None, None)


def test_chat_completion_unknown_model(mock_backend_factory):
    def handler(request):
        if str(request.url).endswith("/v1/models"):
            return httpx.Response(200, json=_models_payload())
        return httpx.Response(404)

    client, backend, auth_headers = mock_backend_factory(handler)
    try:
        resp = client.post(
            "/v1/chat/completions",
            headers=auth_headers,
            json={
                "model": "nonexistent-model",
                "messages": [{"role": "user", "content": "Hi"}],
            },
        )

        assert resp.status_code == 404
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == "model_not_found"
    finally:
        client.__exit__(None, None, None)


def test_chat_completion_missing_model(mock_backend_factory):
    def handler(request):
        if str(request.url).endswith("/v1/models"):
            return httpx.Response(200, json=_models_payload())
        return httpx.Response(404)

    client, backend, auth_headers = mock_backend_factory(handler)
    try:
        resp = client.post(
            "/v1/chat/completions",
            headers=auth_headers,
            json={"messages": [{"role": "user", "content": "Hi"}]},
        )

        assert resp.status_code == 422
    finally:
        client.__exit__(None, None, None)


# ---- Backend errors --------------------------------------------------------


def test_chat_completion_backend_timeout(mock_backend_factory):
    def handler(request):
        if str(request.url).endswith("/v1/models"):
            return httpx.Response(200, json=_models_payload())
        if str(request.url).endswith("/v1/chat/completions"):
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(404)

    client, backend, auth_headers = mock_backend_factory(handler)
    try:
        resp = client.post(
            "/v1/chat/completions",
            headers=auth_headers,
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hi"}],
            },
        )

        assert resp.status_code == 502
        data = resp.json()
        assert data["error"]["code"] == "backend_timeout"
    finally:
        client.__exit__(None, None, None)


def test_chat_completion_backend_unavailable(mock_backend_factory):
    def handler(request):
        if str(request.url).endswith("/v1/models"):
            return httpx.Response(200, json=_models_payload())
        if str(request.url).endswith("/v1/chat/completions"):
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(404)

    client, backend, auth_headers = mock_backend_factory(handler)
    try:
        resp = client.post(
            "/v1/chat/completions",
            headers=auth_headers,
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hi"}],
            },
        )

        assert resp.status_code == 502
        data = resp.json()
        assert data["error"]["code"] == "backend_unavailable"
    finally:
        client.__exit__(None, None, None)


def test_chat_completion_backend_protocol_error(mock_backend_factory):
    def handler(request):
        if str(request.url).endswith("/v1/models"):
            return httpx.Response(200, json=_models_payload())
        if str(request.url).endswith("/v1/chat/completions"):
            return httpx.Response(500, text="internal server error")
        return httpx.Response(404)

    client, backend, auth_headers = mock_backend_factory(handler)
    try:
        resp = client.post(
            "/v1/chat/completions",
            headers=auth_headers,
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hi"}],
            },
        )

        assert resp.status_code == 502
        data = resp.json()
        assert data["error"]["code"] == "backend_protocol_error"
    finally:
        client.__exit__(None, None, None)


def test_chat_completion_backend_invalid_json(mock_backend_factory):
    def handler(request):
        if str(request.url).endswith("/v1/models"):
            return httpx.Response(200, json=_models_payload())
        if str(request.url).endswith("/v1/chat/completions"):
            return httpx.Response(200, text="not valid json")
        return httpx.Response(404)

    client, backend, auth_headers = mock_backend_factory(handler)
    try:
        resp = client.post(
            "/v1/chat/completions",
            headers=auth_headers,
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "Hi"}],
            },
        )

        assert resp.status_code == 502
        data = resp.json()
        assert data["error"]["code"] == "backend_invalid_response"
    finally:
        client.__exit__(None, None, None)
