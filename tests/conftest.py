"""Shared pytest fixtures for Syn M0 tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    """Isolated settings for tests (testing environment, in-memory DB)."""
    return Settings(
        app_name="Syn",
        app_version="0.1.0",
        environment="testing",
        host="127.0.0.1",
        port=8001,
        database_url="sqlite:///:memory:",
        log_level="INFO",
    )


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    """FastAPI test client that invokes the application lifespan."""
    with TestClient(app) as c:
        yield c