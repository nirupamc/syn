"""Shared pytest fixtures for Syn M0 tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    """Isolated settings for tests (testing environment, in-memory DB).

    The backend points at a closed loopback port so health/model probes are
    deterministic (the backend is reported as unreachable) and never depend on
    whether a real llama.cpp happens to be running on the dev machine.
    """
    return Settings(
        app_name="Syn",
        app_version="0.1.0",
        environment="testing",
        host="127.0.0.1",
        port=8001,
        database_url="sqlite:///:memory:",
        log_level="INFO",
        backend_type="llama_cpp",
        backend_base_url="http://127.0.0.1:59999",
        backend_timeout_seconds=120.0,
        backend_connect_timeout_seconds=1.0,
        backend_health_timeout_seconds=1.0,
    )


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    """FastAPI test client that invokes the application lifespan."""
    with TestClient(app) as c:
        yield c