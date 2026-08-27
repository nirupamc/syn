"""M2+M3 isolated tests for /v1/models endpoint.

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

    Returns a function that takes a handler and returns a (client, backend, db) tuple.
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

        # Use a lifespan that injects our mock backend
        from contextlib import asynccontextmanager
        from app.db import Database
        from app.logging import get_logger

        logger = get_logger("syn.test")

        @asynccontextmanager
        async def test_lifespan(fastapi_app):
            # Database
            db = Database(settings.database_url)
            db.connect()
            # Create M3 tables
            import app.models  # noqa: F401
            from app.db.base import Base

            Base.metadata.create_all(bind=db.engine)
            fastapi_app.state.database = db
            # Inject our mock backend
            await backend.open()
            fastapi_app.state.backend = backend
            logger.info("test app started with mock backend")
            try:
                yield
            finally:
                logger.info("test app shutting down")
                await backend.close()
                db.dispose()

        # Replace the lifespan
        app.router.lifespan_context = test_lifespan
        backends_to_close.append(backend)

        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()  # Trigger lifespan startup

        # Create a test user/client/api-key for auth
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

    # Cleanup: close any remaining backends
    for backend in backends_to_close:
        try:
            asyncio.run(backend.close())
        except Exception:
            pass


def _models_payload(models_list=None):
    if models_list is None:
        models_list = [
            {
                "id": "models/gemma-4-E2B-it-Q4_K_M.gguf",
                "object": "model",
                "owned_by": "llamacpp",
                "created": 1787840342,
            }
        ]
    return {
        "object": "list",
        "data": models_list,
    }


# ---- GET /v1/models ---------------------------------------------------------


def test_models_endpoint_requires_auth(mock_backend_factory):
    """GET /v1/models without auth returns 401."""
    def handler(request):
        return httpx.Response(200, json=_models_payload())

    client, backend, _ = mock_backend_factory(handler)
    try:
        resp = client.get("/v1/models")
        assert resp.status_code == 401
    finally:
        client.__exit__(None, None, None)


def test_models_endpoint_returns_models(mock_backend_factory):
    def handler(request):
        assert str(request.url).endswith("/v1/models")
        return httpx.Response(200, json=_models_payload())

    client, backend, auth_headers = mock_backend_factory(handler)
    try:
        resp = client.get("/v1/models", headers=auth_headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == "models/gemma-4-E2B-it-Q4_K_M.gguf"
        assert data["data"][0]["owned_by"] == "llamacpp"
    finally:
        client.__exit__(None, None, None)


def test_models_endpoint_empty_list(mock_backend_factory):
    def handler(request):
        return httpx.Response(200, json=_models_payload([]))

    client, backend, auth_headers = mock_backend_factory(handler)
    try:
        resp = client.get("/v1/models", headers=auth_headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        assert data["data"] == []
    finally:
        client.__exit__(None, None, None)


def test_models_endpoint_backend_unavailable(mock_backend_factory):
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    client, backend, auth_headers = mock_backend_factory(handler)
    try:
        resp = client.get("/v1/models", headers=auth_headers)

        assert resp.status_code == 502
        data = resp.json()
        assert "error" in data
        assert data["error"]["type"] == "server_error"
        assert data["error"]["code"] == "backend_unavailable"
    finally:
        client.__exit__(None, None, None)


def test_models_endpoint_backend_timeout(mock_backend_factory):
    def handler(request):
        raise httpx.ReadTimeout("timeout", request=request)

    client, backend, auth_headers = mock_backend_factory(handler)
    try:
        resp = client.get("/v1/models", headers=auth_headers)

        assert resp.status_code == 502
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == "backend_timeout"
    finally:
        client.__exit__(None, None, None)


def test_models_endpoint_backend_protocol_error(mock_backend_factory):
    def handler(request):
        return httpx.Response(500, text="internal server error")

    client, backend, auth_headers = mock_backend_factory(handler)
    try:
        resp = client.get("/v1/models", headers=auth_headers)

        assert resp.status_code == 502
        data = resp.json()
        assert data["error"]["code"] == "backend_protocol_error"
    finally:
        client.__exit__(None, None, None)


def test_models_endpoint_invalid_json(mock_backend_factory):
    def handler(request):
        return httpx.Response(200, text="not json")

    client, backend, auth_headers = mock_backend_factory(handler)
    try:
        resp = client.get("/v1/models", headers=auth_headers)

        assert resp.status_code == 502
        data = resp.json()
        assert data["error"]["code"] == "backend_invalid_response"
    finally:
        client.__exit__(None, None, None)


def test_models_endpoint_missing_data_array(mock_backend_factory):
    def handler(request):
        return httpx.Response(200, json={"object": "list"})

    client, backend, auth_headers = mock_backend_factory(handler)
    try:
        resp = client.get("/v1/models", headers=auth_headers)

        assert resp.status_code == 502
        data = resp.json()
        assert data["error"]["code"] == "backend_invalid_response"
    finally:
        client.__exit__(None, None, None)


def test_models_endpoint_request_id_header(mock_backend_factory):
    def handler(request):
        return httpx.Response(200, json=_models_payload([]))

    client, backend, auth_headers = mock_backend_factory(handler)
    try:
        resp = client.get(
            "/v1/models",
            headers={**auth_headers, "X-Request-ID": "test-req-123"},
        )

        assert resp.status_code == 200
        assert resp.headers.get("X-Request-ID") == "test-req-123"
    finally:
        client.__exit__(None, None, None)
