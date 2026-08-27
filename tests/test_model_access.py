"""M3 tests: model access policy enforcement."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.backends.llama_cpp import LlamaCppBackend
from app.config import Settings
from app.main import create_app
from app.services import admin as admin_service


@pytest.fixture
def restricted_auth_env(tmp_path):
    """Set up an app with a client restricted to one model and a separate key."""
    backends = []

    def _factory():
        # Two models in backend
        def handler(request):
            if str(request.url).endswith("/v1/models"):
                return httpx.Response(
                    200,
                    json={
                        "object": "list",
                        "data": [
                            {
                                "id": "allowed-model",
                                "object": "model",
                                "owned_by": "llamacpp",
                                "created": 1,
                            },
                            {
                                "id": "forbidden-model",
                                "object": "model",
                                "owned_by": "llamacpp",
                                "created": 2,
                            },
                        ],
                    },
                )
            if str(request.url).endswith("/v1/chat/completions"):
                return httpx.Response(
                    200,
                    json={
                        "id": "cmpl-1",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "allowed-model",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "ok",
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                    },
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        backend = LlamaCppBackend(
            base_url="http://127.0.0.1:8080",
            timeout_seconds=5.0,
            connect_timeout_seconds=1.0,
            health_timeout_seconds=1.0,
            transport=transport,
        )
        backends.append(backend)

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
            try:
                yield
            finally:
                await backend.close()
                db.dispose()

        app.router.lifespan_context = test_lifespan

        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()

        # Create a user
        session = app.state.database.session_factory()
        try:
            user = admin_service.create_user(session, "test-user")
            # Create a restricted client
            restricted_client = admin_service.create_client(
                session,
                user_id=user.id,
                name="restricted",
                allowed_models=["allowed-model"],
            )
            # Create an unrestricted client
            unrestricted_client = admin_service.create_client(
                session, user_id=user.id, name="unrestricted"
            )
            # Issue keys for both
            _, restricted_token = admin_service.create_api_key(
                session, client_id=restricted_client.id, name="rk"
            )
            _, unrestricted_token = admin_service.create_api_key(
                session, client_id=unrestricted_client.id, name="uk"
            )
        finally:
            session.close()

        return {
            "client": client,
            "restricted_token": restricted_token,
            "unrestricted_token": unrestricted_token,
        }

    yield _factory

    for b in backends:
        import asyncio

        asyncio.run(b.close())


# ---- /v1/models filtering ---------------------------------------------------


def test_v1_models_filters_by_access(restricted_auth_env):
    env = restricted_auth_env()
    try:
        resp = env["client"].get(
            "/v1/models",
            headers={"Authorization": f"Bearer {env['restricted_token']}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        ids = {m["id"] for m in data["data"]}
        assert ids == {"allowed-model"}
    finally:
        env["client"].__exit__(None, None, None)


def test_v1_models_unrestricted_sees_all(restricted_auth_env):
    env = restricted_auth_env()
    try:
        resp = env["client"].get(
            "/v1/models",
            headers={"Authorization": f"Bearer {env['unrestricted_token']}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        ids = {m["id"] for m in data["data"]}
        assert ids == {"allowed-model", "forbidden-model"}
    finally:
        env["client"].__exit__(None, None, None)


# ---- /v1/chat/completions enforcement ----------------------------------------


def test_chat_with_allowed_model_succeeds(restricted_auth_env):
    env = restricted_auth_env()
    try:
        resp = env["client"].post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {env['restricted_token']}"},
            json={
                "model": "allowed-model",
                "messages": [{"role": "user", "content": "Hi"}],
            },
        )
        assert resp.status_code == 200
    finally:
        env["client"].__exit__(None, None, None)


def test_chat_with_forbidden_model_returns_403(restricted_auth_env):
    env = restricted_auth_env()
    try:
        resp = env["client"].post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {env['restricted_token']}"},
            json={
                "model": "forbidden-model",
                "messages": [{"role": "user", "content": "Hi"}],
            },
        )
        assert resp.status_code == 403
        data = resp.json()
        assert data["error"]["code"] == "model_forbidden"
        assert data["error"]["param"] == "model"
    finally:
        env["client"].__exit__(None, None, None)


def test_chat_unrestricted_can_use_forbidden_model(restricted_auth_env):
    env = restricted_auth_env()
    try:
        resp = env["client"].post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {env['unrestricted_token']}"},
            json={
                "model": "forbidden-model",
                "messages": [{"role": "user", "content": "Hi"}],
            },
        )
        assert resp.status_code == 200
    finally:
        env["client"].__exit__(None, None, None)
