"""Shared pytest fixtures for Syn tests."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core import api_keys
from app.main import create_app
from app.services import admin as admin_service


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Isolated settings for tests (testing environment, file-based SQLite DB).

    Uses a per-test temp directory so the SQLite database is shared across
    the app's engine and any test-side sessions. The backend points at a
    closed loopback port so health/model probes are deterministic (the
    backend is reported as unreachable) and never depend on whether a real
    llama.cpp happens to be running on the dev machine.
    """
    db_path = tmp_path / "test.db"
    return Settings(
        app_name="Syn",
        app_version="0.1.0",
        environment="testing",
        host="127.0.0.1",
        port=8001,
        database_url=f"sqlite:///{db_path}",
        log_level="INFO",
        backend_type="llama_cpp",
        backend_base_url="http://127.0.0.1:59999",
        backend_timeout_seconds=120.0,
        backend_connect_timeout_seconds=1.0,
        backend_health_timeout_seconds=1.0,
        admin_secret="test-admin-secret",
    )


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    """FastAPI test client that invokes the application lifespan."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def test_principal(app):
    """Create a test user/client/api-key and return (full_token, user, client).

    The API key is unrestricted (all models allowed).
    """
    db = app.state.database
    session = db.session_factory()
    try:
        user = admin_service.create_user(session, "test-user")
        client_obj = admin_service.create_client(
            session, user_id=user.id, name="test-client"
        )
        api_key, full_token = admin_service.create_api_key(
            session, client_id=client_obj.id, name="test-key"
        )
        return {
            "user": user,
            "client": client_obj,
            "api_key": api_key,
            "token": full_token,
        }
    finally:
        session.close()


@pytest.fixture
def auth_headers(test_principal) -> dict[str, str]:
    """Return Authorization headers with a valid test API key."""
    return {"Authorization": f"Bearer {test_principal['token']}"}


@pytest.fixture
def auth_env(client):
    """Return a dict with the test client, token, user, client_obj, and api_key.

    Uses the shared `client` fixture so the lifespan has started and the
    database is wired onto app.state.
    """
    db = client.app.state.database
    session = db.session_factory()
    try:
        user = admin_service.create_user(session, "test-user")
        client_obj = admin_service.create_client(
            session, user_id=user.id, name="test-client"
        )
        api_key, full_token = admin_service.create_api_key(
            session, client_id=client_obj.id, name="test-key"
        )
    finally:
        session.close()

    return {
        "client": client,
        "token": full_token,
        "user": user,
        "client_obj": client_obj,
        "api_key": api_key,
    }