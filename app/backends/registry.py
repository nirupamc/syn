"""Backend registry: maps configured BackendType to backend classes.

The registry decouples configuration from concrete backend implementations.
New backends register themselves here (or via the declared set), so the API
and service layers never import concrete backend classes directly.
"""

from __future__ import annotations

from typing import Optional, Type

from app.config import BackendType
from app.core.errors import ConfigurationError

from .base import InferenceBackend


class RegistryError(ConfigurationError):
    """Raised when a backend cannot be resolved/created from configuration."""


# M1 registers the llama.cpp backend (implemented connectivity).
_REGISTRY: dict[BackendType, Type[InferenceBackend]] = {}


def register(type_: BackendType, cls: Type[InferenceBackend]) -> None:
    """Register a backend class for a configured BackendType."""
    _REGISTRY[type_] = cls


def get_backend_class(type_: BackendType) -> Type[InferenceBackend]:
    """Return the registered class for a BackendType or raise RegistryError."""
    cls = _REGISTRY.get(type_)
    if cls is None:
        raise RegistryError(
            f"no backend registered for type {type_.value!r}",
            code="backend_not_registered",
        )
    return cls


def build_backend(
    type_: BackendType,
    base_url: str,
    *,
    timeout_seconds: float = 120.0,
    connect_timeout_seconds: float = 10.0,
    health_timeout_seconds: float = 5.0,
    transport: Optional[object] = None,
) -> InferenceBackend:
    """Instantiate the backend configured for ``type_``.

    Timeout values and an optional test transport are forwarded so the whole
    backend is configured from a single call site (the app lifespan).
    """
    cls = get_backend_class(type_)
    return cls(
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        connect_timeout_seconds=connect_timeout_seconds,
        health_timeout_seconds=health_timeout_seconds,
        transport=transport,
    )


def registered_backend_types() -> tuple[BackendType, ...]:
    """Return the sorted set of registered backend types."""
    return tuple(sorted(_REGISTRY, key=lambda t: t.value))