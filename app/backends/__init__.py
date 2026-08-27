"""Inference backend package.

M0 defines the backend abstraction and a registry so that the rest of Syn
discovers backends by configured type without direct imports of concrete
classes. The concrete llama.cpp backend placeholder exists only to prove the
seam; actual integration is M1.
"""

from app.backends.base import (
    BackendCapability,
    BackendInfo,
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
    "BackendInfo",
    "InferenceBackend",
    "LlamaCppBackend",
    "RegistryError",
    "build_backend",
    "get_backend_class",
    "registered_backend_types",
]