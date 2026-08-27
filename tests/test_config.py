"""Typed configuration behavior tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import BackendType, Environment, Settings


def test_defaults():
    s = Settings()
    assert s.app_name == "Syn"
    assert s.app_version == "0.1.0"
    assert s.environment == Environment.DEVELOPMENT
    assert s.backend_type == BackendType.LLAMA_CPP
    assert s.backend_base_url == "http://127.0.0.1:8080"
    assert s.backend_timeout_seconds == 120.0


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("SYN_PORT", "9090")
    monkeypatch.setenv("SYN_BACKEND_BASE_URL", "http://127.0.0.1:9999")
    s = Settings()
    assert s.port == 9090
    assert s.backend_base_url == "http://127.0.0.1:9999"


def test_environment_enum():
    assert Settings(environment="testing").environment == Environment.TESTING


def test_validate_defaults_schema_shape(settings):
    d = settings.model_dump()
    assert d["port"] == 8001
    assert d["environment"] == "testing"


def test_invalid_port_rejected():
    with pytest.raises(ValidationError):
        Settings(port=0)
    with pytest.raises(ValidationError):
        Settings(port=70000)


def test_invalid_log_level_rejected():
    with pytest.raises(ValidationError):
        Settings(log_level="loud")


def test_non_positive_backend_timeout_rejected():
    with pytest.raises(ValidationError):
        Settings(backend_timeout_seconds=0)
    with pytest.raises(ValidationError):
        Settings(backend_timeout_seconds=-5)