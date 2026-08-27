"""llama.cpp backend (M1).

Implements the Syn backend abstraction against a private, local
``llama-server`` exposing an OpenAI-compatible API on loopback (e.g.
``http://127.0.0.1:8080``).

In this milestone we implement *backend connectivity only*:

* ``health()``     — real probe of the llama.cpp ``/health`` endpoint
* ``models()``     — real discovery via the OpenAI-compatible ``/v1/models``
* ``capabilities()`` — ``HEALTH`` and ``MODELS`` (justified by implementation)

Chat completions, streaming and cancellation are NOT implemented yet (M2/M5).

All llama.cpp HTTP details live in this module, behind the backend
abstraction. Other Syn code never imports llama.cpp specifics.

The configured URL comes from typed configuration (``SYN_BACKEND_BASE_URL``).
The llama.cpp server must remain loopback/private; we never bind, expose, or
tunnel it.
"""

from __future__ import annotations

import datetime as _dt
import time
from typing import Optional

import httpx

from app.backends.base import (
    BackendCapability,
    BackendHealthResult,
    BackendHealthState,
    BackendInfo,
    BackendModelInfo,
    InferenceBackend,
)
from app.config import BackendType
from app.core.errors import (
    BackendInvalidResponseError,
    BackendProtocolError,
    BackendTimeoutError,
    BackendUnavailableError,
)
from app.logging import get_logger

logger = get_logger("syn.backend.llama_cpp")


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


class LlamaCppBackend(InferenceBackend):
    """Adapter for a private llama.cpp ``llama-server`` instance."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8080",
        timeout_seconds: float = 120.0,
        connect_timeout_seconds: float = 10.0,
        health_timeout_seconds: float = 5.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        super().__init__(
            base_url,
            timeout_seconds=timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            health_timeout_seconds=health_timeout_seconds,
        )
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None
        self._last_state: Optional[BackendHealthState] = None

    # -- identity ------------------------------------------------------------

    @property
    def name(self) -> str:
        return "llama_cpp"

    @property
    def backend_info(self) -> BackendInfo:
        return BackendInfo(
            name=self.name,
            display_name="llama.cpp",
            base_url=self.base_url,
            capabilities=self.capabilities(),
            timeout_seconds=self.timeout_seconds,
        )

    def capabilities(self) -> tuple[BackendCapability, ...]:
        """Only capabilities actually implemented in this milestone."""
        return (BackendCapability.HEALTH, BackendCapability.MODELS)

    # -- client lifecycle ----------------------------------------------------

    def _client_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            timeout=self.timeout_seconds,
            connect=self.connect_timeout_seconds,
        )

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            kwargs: dict[str, object] = {"timeout": self._client_timeout()}
            if self._transport is not None:
                kwargs["transport"] = self._transport
            else:
                kwargs["base_url"] = self.base_url
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def open(self) -> None:
        self._ensure_client()
        logger.info("llama.cpp backend client opened (%s)", self.base_url)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.info("llama.cpp backend client closed (%s)", self.base_url)

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _classify_transport_error(exc: httpx.TransportError) -> str:
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            return "connection failed"
        if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout)):
            return "timed out"
        if isinstance(exc, (httpx.ProtocolError, httpx.RemoteProtocolError)):
            return "protocol error"
        return type(exc).__name__
    def _record_health(
        self,
        state: BackendHealthState,
        reason: str,
        elapsed_ms: Optional[float],
        *,
        server_version: Optional[str] = None,
        model: Optional[str] = None,
    ) -> BackendHealthResult:
        result = BackendHealthResult(
            state=state,
            reachable=(state is BackendHealthState.REACHABLE),
            configured=True,
            reason=reason,
            elapsed_ms=elapsed_ms,
            server_version=server_version,
            model=model,
            last_checked=_utcnow(),
        )
        # Log only on *state transitions* to avoid noisy repeated health polls.
        prev = self._last_state
        if prev is not state:
            if state is BackendHealthState.REACHABLE:
                if prev is not None and prev is not BackendHealthState.REACHABLE:
                    logger.info("backend health recovered (%s)", self.base_url)
            elif prev is not None:
                logger.warning(
                    "backend health=%s reason=%s (%s)",
                    state.value,
                    reason,
                    self.base_url,
                )
            self._last_state = state
        return result

    # -- health -----------------------------------------------------------------

    async def health(self) -> BackendHealthResult:
        started = time.monotonic()
        client = self._ensure_client()
        # Health probes use their own shorter timeout so polls stay responsive.
        health_timeout = httpx.Timeout(timeout=self.health_timeout_seconds)
        try:
            resp = await client.get(f"{self.base_url}/health", timeout=health_timeout)
        except httpx.TimeoutException:
            elapsed = (time.monotonic() - started) * 1000
            return self._record_health(
                BackendHealthState.TIMEOUT,
                "health probe timed out",
                elapsed,
            )
        except httpx.TransportError as exc:  # noqa: BLE001 - typed result
            elapsed = (time.monotonic() - started) * 1000
            return self._record_health(
                BackendHealthState.UNREACHABLE,
                f"unreachable: {self._classify_transport_error(exc)}",
                elapsed,
            )
        elapsed = (time.monotonic() - started) * 1000

        if resp.status_code == 503:
            return self._record_health(
                BackendHealthState.UNREACHABLE,
                "server alive but model not ready (503)",
                elapsed,
            )
        if resp.status_code != 200:
            return self._record_health(
                BackendHealthState.UNREACHABLE,
                f"unexpected health status {resp.status_code}",
                elapsed,
            )

        try:
            payload = resp.json()
        except Exception:  # noqa: BLE001 - malformed body
            return self._record_health(
                BackendHealthState.INVALID,
                "invalid JSON from health endpoint",
                elapsed,
            )
        if isinstance(payload, dict) and payload.get("status") == "ok":
            return self._record_health(
                BackendHealthState.REACHABLE,
                "healthy",
                elapsed,
                server_version=(
                    payload.get("version") if isinstance(payload, dict) else None
                ),
            )
        label = payload.get("status") if isinstance(payload, dict) else payload
        return self._record_health(
            BackendHealthState.INVALID,
            f"unexpected health payload: {str(label)[:80]!r}",
            elapsed,
        )

    # -- model discovery --------------------------------------------------------

    async def models(self) -> list[BackendModelInfo]:
        client = self._ensure_client()
        try:
            resp = await client.get(f"{self.base_url}/v1/models")
        except httpx.TimeoutException as exc:
            raise BackendTimeoutError(
                "model discovery timed out",
                code="backend_timeout",
            ) from exc
        except httpx.TransportError as exc:
            raise BackendUnavailableError(
                f"model discovery unreachable: {self._classify_transport_error(exc)}",
                code="backend_unavailable",
            ) from exc

        if resp.status_code != 200:
            raise BackendProtocolError(
                f"model discovery returned HTTP {resp.status_code}",
                code="backend_protocol_error",
            )

        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise BackendInvalidResponseError(
                "invalid JSON from /v1/models",
                code="backend_invalid_response",
            ) from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise BackendInvalidResponseError(
                "unexpected shape from /v1/models (no data array)",
                code="backend_invalid_response",
            )

        models: list[BackendModelInfo] = []
        for item in data:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            models.append(
                BackendModelInfo(
                    id=str(item["id"]),
                    object=item.get("object") or "model",
                    owned_by=item.get("owned_by") or "llamacpp",
                    created=item.get("created"),
                )
            )
        return models


# Register eagerly so the registry resolves llama_cpp from configuration.
from app.backends.registry import register  # noqa: E402

register(BackendType.LLAMA_CPP, LlamaCppBackend)  # noqa: F821