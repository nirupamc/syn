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
from typing import Any, AsyncIterator, Optional

from app.schemas.chat import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
)


class BackendCapability(StrEnum):
    """Capabilities a backend may advertise.

    Capabilities are only claimed when Syn actually implements the backing
    capability for that backend. In M1, ``HEALTH`` and ``MODELS`` are real.
    ``CHAT_COMPLETIONS``/``STREAMING``/``CANCELLATION`` are claimed only once
    the corresponding lifecycle methods are implemented (M2/M5).
    """

    HEALTH = "health"
    MODELS = "models"
    CHAT_COMPLETIONS = "chat_completions"
    STREAMING = "streaming"
    CANCELLATION = "cancellation"


class BackendHealthState(StrEnum):
    """Operational state of a backend as determined by a health probe."""

    UNKNOWN = "unknown"        # no probe has run yet
    REACHABLE = "reachable"   # health probe succeeded
    UNREACHABLE = "unreachable"  # connection-level failure / refused / not ready
    TIMEOUT = "timeout"       # probe exceeded the health timeout
    INVALID = "invalid"       # probe returned an unparseable/contradictory body


@dataclass(frozen=True)
class BackendHealthResult:
    """Typed, safe result of a backend health probe.

    Contains only operational information intended for health reporting and
    logging. Never holds filesystem paths, secrets, or raw stack traces.
    """

    state: BackendHealthState
    reachable: bool
    configured: bool = True
    reason: str = ""
    elapsed_ms: Optional[float] = None
    server_version: Optional[str] = None
    model: Optional[str] = None
    last_checked: Optional[str] = None  # ISO-8601 UTC timestamp


@dataclass(frozen=True)
class BackendModelInfo:
    """Normalized, typed description of a model served by a backend.

    Syn code should consume this type instead of raw backend response dicts.
    A full model registry / router is future work (M9).
    """

    id: str
    object: str = "model"
    owned_by: str = "llamacpp"
    created: Optional[int] = None


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

    Lifecycle methods, implemented per-milestone:

    * ``health()``                  - backend liveness (M1)
    * ``models()``                  - list served models (M1)
    * ``chat_completion()``         - single (non-streamed) completion (M2)
    * ``stream_chat_completion()``  - streamed completion (M5)
    * ``cancel()``                  - cancel an in-flight request (M5)

    Backends should implement the HTTP-client lifecycle via ``open()`` /
    ``close()`` so a single client is reused and torn down cleanly.
    """

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 120.0,
        *,
        connect_timeout_seconds: float = 10.0,
        health_timeout_seconds: float = 5.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.connect_timeout_seconds = connect_timeout_seconds
        self.health_timeout_seconds = health_timeout_seconds

    @property
    @abstractmethod
    def name(self) -> str:
        """Canonical backend identifier (matches BackendType value)."""

    @property
    @abstractmethod
    def backend_info(self) -> BackendInfo:
        """Static description of this backend."""

    def capabilities(self) -> tuple[BackendCapability, ...]:
        """Advertised capabilities of this backend (empty unless supported)."""
        return ()

    # ---- HTTP client lifecycle ------------------------------------------

    async def open(self) -> None:  # noqa: B027 (optional override)
        """Prepare reusable resources (e.g. an HTTP client). No-op by default."""

    async def close(self) -> None:  # noqa: B027
        """Release reusable resources. No-op by default."""

    # ---- Backend operations ---------------------------------------------

    async def health(self) -> BackendHealthResult:
        """Probe backend health and return a safe result (never raises)."""
        raise NotImplementedError

    async def models(self) -> list[BackendModelInfo]:
        """Discover served models, normalized into Syn types."""
        raise NotImplementedError

    async def chat_completion(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        raise NotImplementedError

    def stream_chat_completion(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        """Stream chat completion chunks for a given request.

        Returns an async iterator yielding ``ChatCompletionChunk`` objects.
        The backend is responsible for consuming its upstream transport
        incrementally and for cleanly closing resources when the iterator
        is closed (e.g. on client disconnect).

        Implementations should raise backend-typed errors
        (``BackendTimeoutError``, ``BackendProtocolError``, etc.) on
        transport-level failures. They MUST NOT leak raw transport
        objects or backend-specific dictionaries to the caller.
        """
        raise NotImplementedError
        yield  # pragma: no cover  -- makes this an async generator

    async def cancel(self, *, request_id: str) -> Any:
        raise NotImplementedError