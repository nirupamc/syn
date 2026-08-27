"""Inference backend package.

M1 implements the llama.cpp backend behind this abstraction. The ``backends``
package is the only place that knows llama.cpp HTTP details; the registry maps
the configured ``BackendType`` to a concrete implementation so no other layer
imports a concrete backend.
"""

from app.backends.base import (
    BackendCapability,
    BackendHealthResult,
    BackendHealthState,
    BackendInfo,
    BackendModelInfo,
    InferenceBackend,
)
from app.backends.llama_cpp import LlamaCppBackend  # noqa: F401  (registers)
from app.backends.registry import (
    RegistryError,
    build_backend,
    get_backend_class,
    registered_backend_types,
)

__all__ = [
    "BackendCapability",
    "BackendHealthResult",
    "BackendHealthState",
    "BackendInfo",
    "BackendModelInfo",
    "InferenceBackend",
    "LlamaCppBackend",
    "RegistryError",
    "build_backend",
    "get_backend_class",
    "registered_backend_types",
]