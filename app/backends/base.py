"""Inference backend abstraction.

Syn code outside this integration layer must never couple directly to
llama.cpp or any other concrete backend. This module defines the deliberate
contract that backends implement, plus the capability surface.

M0 intentionally keeps this small. The contract is a trailing set of
*planned* methods on the abstract base; the concrete llama.cpp integration
lands in M1. Nothing here makes a real HTTP request to a backend.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Optional


class BackendCapability(StrEnum):
    """Capabilities a backend may advertise.

    These are the eventual capabilities Syn can rely on from a backend.
    In M0 none are claimed as implemented by any concrete backend.
    """

    HEALTH = "health"
    MODELS = "models"
    CHAT_COMPLETIONS = "chat_completions"
    STREAMING = "streaming"
    CANCELLATION = "cancellation"


@dataclass(frozen=True)
class BackendInfo:
    """Immutable description of a configured backend."""

    name: str                      # canonical name, e.g. "llama_cpp"
    display_name: str
    base_url: str                  # address the backend listens on
    capabilities: tuple[BackendCapability, ...] = ()
    timeout_seconds: float = 120.0


class InferenceBackend(ABC):
    """Contract every Syn inference backend must satisfy.

    Planned lifecycle methods (implemented per-milestone, currently raise
    ``NotImplementedError``):

    * ``health()``                  - backend liveness (M1)
    * ``models()``                  - list served models (M1)
    * ``chat_completion()``         - single (non-streamed) completion (M2)
    * ``stream_chat_completion()``  - streamed completion (M5)
    * ``cancel()``                  - cancel an in-flight request (M5)

    Only ``capabilities()`` and property access are required in M0.
    """

    def __init__(self, base_url: str, timeout_seconds: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @property
    @abstractmethod
    def name(self) -> str:
        """Canonical backend identifier (matches BackendType value)."""

    @property
    @abstractmethod
    def backend_info(self) -> BackendInfo:
        """Static description of this backend."""

    def capabilities(self) -> tuple[BackendCapability, ...]:
        """Advertised capabilities of this backend (empty in M0)."""
        return ()

    # ---- Planned lifecycle methods; implemented from M1 onward -----------

    async def health(self) -> Any:
        raise NotImplementedError

    async def models(self) -> Any:
        raise NotImplementedError

    async def chat_completion(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def stream_chat_completion(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def cancel(self, *, request_id: str) -> Any:
        raise NotImplementedError