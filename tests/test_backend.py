"""Backend abstraction / contract / registry tests.

These must never require a running llama.cpp instance or network access.
"""

from __future__ import annotations

import pytest
from enum import StrEnum

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


def test_backend_capabilities_empty_in_m0():
    """No backend claims capabilities before the relevant milestone."""
    backend = build_backend(BackendType.LLAMA_CPP, "http://127.0.0.1:8080")
    assert backend.capabilities() == ()


def test_backend_info_shape():
    backend = build_backend(BackendType.LLAMA_CPP, "http://example:8080", timeout_seconds=30)
    info = backend.backend_info
    assert info.name == "llama_cpp"
    assert info.base_url == "http://example:8080"
    assert info.timeout_seconds == 30
    assert info.capabilities == ()


def test_lifecycle_methods_unimplemented_in_m0():
    backend = build_backend(BackendType.LLAMA_CPP, "http://127.0.0.1:8080")
    for method in (
        backend.health,
        backend.models,
        backend.chat_completion,
        backend.stream_chat_completion,
    ):
        # They must exist but be explicitly not implemented — nothing is
        # falsely claimed as working in M0.
        assert callable(method)


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