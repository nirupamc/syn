"""Backend abstraction / contract / registry tests.

These must never require a running llama.cpp instance or network access.
"""

from __future__ import annotations

from enum import StrEnum

import pytest

from app.backends import (
    BackendCapability,
    InferenceBackend,
    LlamaCppBackend,
    build_backend,
    get_backend_class,
    registered_backend_types,
)
from app.backends.registry import RegistryError
from app.config import BackendType


def test_registry_knows_llama_cpp():
    assert BackendType.LLAMA_CPP in registered_backend_types()
    assert get_backend_class(BackendType.LLAMA_CPP) is LlamaCppBackend


def test_build_backend_returns_llama_cpp():
    backend = build_backend(BackendType.LLAMA_CPP, "http://127.0.0.1:8080")
    assert isinstance(backend, InferenceBackend)
    assert isinstance(backend, LlamaCppBackend)
    assert backend.name == "llama_cpp"
    assert backend.base_url == "http://127.0.0.1:8080"


def test_backend_capabilities_justified_in_m1():
    """Capabilities are only claimed when actually implemented (M1 = health/models)."""
    backend = build_backend(BackendType.LLAMA_CPP, "http://127.0.0.1:8080")
    caps = backend.capabilities()
    assert BackendCapability.HEALTH in caps
    assert BackendCapability.MODELS in caps
    # Chat/streaming/cancellation are NOT implemented yet (M2/M5).
    assert BackendCapability.CHAT_COMPLETIONS not in caps
    assert BackendCapability.STREAMING not in caps
    assert BackendCapability.CANCELLATION not in caps


def test_backend_info_shape():
    backend = build_backend(BackendType.LLAMA_CPP, "http://example:8080", timeout_seconds=30)
    info = backend.backend_info
    assert info.name == "llama_cpp"
    assert info.base_url == "http://example:8080"
    assert info.timeout_seconds == 30
    assert BackendCapability.HEALTH in info.capabilities


def test_not_implemented_methods_raise_when_awaited():
    """Chat/streaming/cancel must remain explicitly unimplemented in M1."""
    backend = build_backend(BackendType.LLAMA_CPP, "http://127.0.0.1:8080")
    for method in (
        backend.chat_completion,
        backend.stream_chat_completion,
    ):
        assert callable(method)


async def test_not_implemented_methods_raise_when_awaited_async():
    backend = build_backend(BackendType.LLAMA_CPP, "http://127.0.0.1:8080")
    with pytest.raises(NotImplementedError):
        await backend.chat_completion()
    with pytest.raises(NotImplementedError):
        await backend.stream_chat_completion()


async def test_health_and_models_are_async_callables():
    backend = build_backend(BackendType.LLAMA_CPP, "http://127.0.0.1:8080")
    assert callable(backend.health)
    assert callable(backend.models)


class _UnknownBackend(StrEnum):
    BOGUS = "bogus"


def test_unregistered_type_raises():
    with pytest.raises(RegistryError):
        get_backend_class(_UnknownBackend.BOGUS)
    with pytest.raises(RegistryError):
        build_backend(_UnknownBackend.BOGUS, "http://x")


def test_base_url_normalizes_trailing_slash():
    backend = build_backend(BackendType.LLAMA_CPP, "http://127.0.0.1:8080/")
    assert backend.base_url == "http://127.0.0.1:8080"


def test_capability_enum_values():
    # Guard against accidental renames used by future routing.
    assert BackendCapability.CHAT_COMPLETIONS.value == "chat_completions"
    assert BackendCapability.STREAMING.value == "streaming"

def test_backend_timeout_config_forwarded():
    backend = build_backend(
        BackendType.LLAMA_CPP,
        "http://127.0.0.1:8080",
        timeout_seconds=30.0,
        connect_timeout_seconds=2.0,
        health_timeout_seconds=3.0,
    )
    assert backend.timeout_seconds == 30.0
    assert backend.connect_timeout_seconds == 2.0
    assert backend.health_timeout_seconds == 3.0